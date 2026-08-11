"""
Unit tests for ingestion/tfl_ingest.py.

transform_records() and _require_env() have no external dependencies at all — no network, no cloud, 
nothing to fake. These tests run as plain function calls: real input in, real output checked, 
nothing more.

fetch_line_status() calls requests.get internally — it reaches out and creates that dependency 
itself, with no way for a test to hand it a substitute directly. 
This required @patch("tfl_ingest.requests.get") — forcibly swapping the real function for a 
MagicMock for the duration of one test, then automatically restoring it afterward.

ensure_dataset_exists() takes client as a parameter instead of creating one internally — dependency 
injection, already built into the function's original design. This needed no @patch at all — just a 
plain MagicMock() handed in directly as an argument, since the function was written from the start 
to accept its dependency from outside rather than reach out and grab it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import tfl_ingest

# ---------------------------------------------------------------------------
# transform_records
# ---------------------------------------------------------------------------

def test_transform_records_single_line_single_status():
    ingested_at = datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)
    raw_lines = [
        {
            "id": "central",
            "name": "Central",
            "lineStatuses": [
                {"statusSeverity": 10, "statusSeverityDescription": "Good Service"}
            ],
        }
    ]

    rows = tfl_ingest.transform_records(raw_lines, ingested_at)

    assert len(rows) == 1
    assert rows[0]["line_id"] == "central"
    assert rows[0]["status_severity_description"] == "Good Service"
    assert rows[0]["reason"] is None
    assert rows[0]["ingested_at"] == ingested_at.isoformat()


def test_transform_records_preserves_concurrent_statuses():
    """A line with two simultaneous statuses must produce two rows, not one.

    This is the real case discovered in the TfL API research: Metropolitan
    showing 'Part Suspended' and 'Special Service' at the same time.
    """
    ingested_at = datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)
    raw_lines = [
        {
            "id": "metropolitan",
            "name": "Metropolitan",
            "lineStatuses": [
                {
                    "statusSeverity": 9,
                    "statusSeverityDescription": "Part Suspended",
                    "reason": "Signal failure at Baker Street",
                },
                {"statusSeverity": 20, "statusSeverityDescription": "Special Service"},
            ],
        }
    ]

    rows = tfl_ingest.transform_records(raw_lines, ingested_at)

    assert len(rows) == 2
    descriptions = {row["status_severity_description"] for row in rows}
    assert descriptions == {"Part Suspended", "Special Service"}
    part_suspended = next(
        r for r in rows if r["status_severity_description"] == "Part Suspended"
    )
    assert part_suspended["reason"] == "Signal failure at Baker Street"


def test_transform_records_missing_reason_defaults_to_none():
    ingested_at = datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)
    raw_lines = [
        {
            "id": "victoria",
            "name": "Victoria",
            "lineStatuses": [
                {"statusSeverity": 10, "statusSeverityDescription": "Good Service"}
            ],
        }
    ]

    rows = tfl_ingest.transform_records(raw_lines, ingested_at)

    assert rows[0]["reason"] is None


def test_transform_records_empty_line_statuses_produces_no_rows():
    """A line with an empty lineStatuses array should be skipped, not crash."""
    ingested_at = datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)
    raw_lines = [{"id": "jubilee", "name": "Jubilee", "lineStatuses": []}]

    rows = tfl_ingest.transform_records(raw_lines, ingested_at)

    assert rows == []


# ---------------------------------------------------------------------------
# _require_env
# ---------------------------------------------------------------------------

def test_require_env_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "hello")
    assert tfl_ingest._require_env("SOME_TEST_VAR") == "hello"


def test_require_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("SOME_MISSING_VAR", raising=False)
    with pytest.raises(RuntimeError, match="SOME_MISSING_VAR"):
        tfl_ingest._require_env("SOME_MISSING_VAR")


# ---------------------------------------------------------------------------
# fetch_line_status — mocked network call, no real API hit
# ---------------------------------------------------------------------------

@patch("tfl_ingest.requests.get")
def test_fetch_line_status_calls_correct_url_and_parses_json(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"id": "central", "name": "Central", "lineStatuses": []}
    ]
    mock_get.return_value = mock_response

    result = tfl_ingest.fetch_line_status(api_key="fake-key")

    mock_get.assert_called_once_with(
        tfl_ingest.TFL_STATUS_URL, params={"app_key": "fake-key"}, timeout=15
    )
    mock_response.raise_for_status.assert_called_once()
    assert result == [{"id": "central", "name": "Central", "lineStatuses": []}]


# ---------------------------------------------------------------------------
# ensure_dataset_exists — mocked BigQuery client, no real GCP call
# ---------------------------------------------------------------------------

def test_ensure_dataset_exists_skips_creation_if_dataset_found():
    mock_client = MagicMock()

    tfl_ingest.ensure_dataset_exists(mock_client, "my-project", "tfl_dev")

    mock_client.get_dataset.assert_called_once()
    mock_client.create_dataset.assert_not_called()


def test_ensure_dataset_exists_creates_dataset_if_missing():
    from google.cloud.exceptions import NotFound

    mock_client = MagicMock()
    mock_client.get_dataset.side_effect = NotFound("not found")

    tfl_ingest.ensure_dataset_exists(mock_client, "my-project", "tfl_dev")

    mock_client.create_dataset.assert_called_once()