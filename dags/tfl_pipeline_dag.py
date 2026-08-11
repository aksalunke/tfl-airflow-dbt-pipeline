"""
TfL Line Status Pipeline DAG.

Runs daily: ingest live tube line status from the TfL API into BigQuery,
then transform it through dbt's staging and mart layers, testing after
each stage. Any failure stops the chain — dbt never runs against a
failed ingest, and mart never builds on untested staging data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

default_args = {
    "owner": "aksha",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="tfl_line_status_pipeline",
    description="Ingest TfL line status, transform via dbt staging + mart",
    default_args=default_args,
    schedule="0 6 * * *",
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["tfl", "portfolio"],
    doc_md=__doc__,
) as dag:

    ingest_tfl_line_status = BashOperator(
        task_id="ingest_tfl_line_status",
        bash_command="python /opt/airflow/ingestion/tfl_ingest.py",
    )

    dbt_run_staging = BashOperator(
        task_id="dbt_run_staging",
        bash_command="dbt run --select staging",
    )

    dbt_test_staging = BashOperator(
        task_id="dbt_test_staging",
        bash_command="dbt test --select staging",
    )

    dbt_run_mart = BashOperator(
        task_id="dbt_run_mart",
        bash_command="dbt run --select mart",
    )

    dbt_test_mart = BashOperator(
        task_id="dbt_test_mart",
        bash_command="dbt test --select mart",
    )

    (
        ingest_tfl_line_status
        >> dbt_run_staging
        >> dbt_test_staging
        >> dbt_run_mart
        >> dbt_test_mart
    )