FROM apache/airflow:3.3.0

COPY ingestion/requirements.txt /tmp/ingestion-requirements.txt

RUN pip install --no-cache-dir -r /tmp/ingestion-requirements.txt \
    && pip install --no-cache-dir dbt-bigquery==1.12.0