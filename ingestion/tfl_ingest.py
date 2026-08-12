"""
TfL Line Status ingestion script.

Fetches the current status of all London Underground (tube) lines from the
TfL Unified API and writes the raw response into a BigQuery table, one row
per (line, status) pair. This is the raw zone of the pipeline: unmodified,
timestamped, append-only.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import requests
from dotenv import load_dotenv
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TFL_STATUS_URL = "https://api.tfl.gov.uk/Line/Mode/tube/Status"
BQ_DATASET = "tfl_dev"
BQ_TABLE = "raw_line_status"
BQ_LOCATION = "EU"

RAW_LINE_STATUS_SCHEMA = [
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("line_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("line_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("status_severity", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("status_severity_description", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("reason", "STRING", mode="NULLABLE"),
]


def _require_env(var_name: str) -> str:
    """Fetch a required environment variable or fail with a clear message."""
    value = os.environ.get(var_name)
    if not value:
        raise RuntimeError(f"Required environment variable '{var_name}' is not set.")
    return value


def fetch_line_status(api_key: str) -> list[dict[str, Any]]:
    """Call the TfL Line Status endpoint and return the parsed JSON response."""
    params = {"app_key": api_key} if api_key else {}
    response = requests.get(TFL_STATUS_URL, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def transform_records(
    raw_lines: list[dict[str, Any]], ingested_at: datetime
) -> list[dict[str, Any]]:
    """Flatten the TfL response into one row per (line, status) pair.

    A line can carry more than one concurrent status (e.g. 'Part Suspended'
    and 'Special Service' at the same time), so every entry in each line's
    lineStatuses array becomes its own row rather than only taking the first.
    """
    rows: list[dict[str, Any]] = []
    for line in raw_lines:
        for status in line.get("lineStatuses", []):
            rows.append(
                {
                    "ingested_at": ingested_at.isoformat(),
                    "line_id": line["id"],
                    "line_name": line["name"],
                    "status_severity": status["statusSeverity"],
                    "status_severity_description": status["statusSeverityDescription"],
                    "reason": status.get("reason"),
                }
            )
    return rows


def ensure_dataset_exists(client: bigquery.Client, project_id: str, dataset_id: str) -> None:
    """Create the target BigQuery dataset if it doesn't already exist."""
    dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        logger.info("Dataset %s.%s not found, creating it.", project_id, dataset_id)
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = BQ_LOCATION
        client.create_dataset(dataset)


def write_to_bigquery(rows: list[dict[str, Any]], project_id: str) -> None:
    """Append rows to the raw_line_status table, creating it if missing."""
    client = bigquery.Client(project=project_id)
    ensure_dataset_exists(client, project_id, BQ_DATASET)

    table_ref = f"{project_id}.{BQ_DATASET}.{BQ_TABLE}"
    job_config = bigquery.LoadJobConfig(
        schema=RAW_LINE_STATUS_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    load_job = client.load_table_from_json(rows, table_ref, job_config=job_config)
    load_job.result()  # blocks until finished, raises on failure
    logger.info("Loaded %d rows into %s", len(rows), table_ref)


def main() -> None:
    api_key = os.environ.get("TFL_API_KEY", "")
    project_id = _require_env("GCP_PROJECT_ID")

    if not api_key:
        logger.warning(
            "TFL_API_KEY not set — proceeding unauthenticated (50 requests/hour limit)."
        )

    ingested_at = datetime.now(UTC)

    logger.info("Fetching tube line status from TfL API...")
    raw_lines = fetch_line_status(api_key)
    logger.info("Received status for %d lines.", len(raw_lines))

    rows = transform_records(raw_lines, ingested_at)
    logger.info("Transformed into %d rows.", len(rows))

    write_to_bigquery(rows, project_id)


if __name__ == "__main__":
    main()