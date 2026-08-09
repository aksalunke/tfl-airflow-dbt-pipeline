# Architecture Decisions — TfL Airflow + dbt Pipeline

## ADR 1 — Python 3.13 over 3.14
**Context:** 3.14 was the newest available version at setup time. 
**Decision:** Installed 3.13 instead.
**Reasoning:** Airflow 3.2+ supports both, but a dbt-core GitHub issue (Oct 2025) documented 3.14 crashing on a mashumaro serialisation bug, with a fix only milestoned for a future release. The BigQuery adapter's 3.14 compatibility wasn't confirmed either. Chose the version with confirmed
support across the full stack over the newest available.

## ADR 2 — LocalExecutor over CeleryExecutor
**Context:** Airflow's official docker-compose.yaml defaults to CeleryExecutor — 7 containers including Redis and a separate worker.
**Decision:** Removed Redis and the worker service; switched to LocalExecutor, where the scheduler runs tasks as local subprocesses. **Reasoning:** Host machine has 7.7GB total RAM. CeleryExecutor's
documented minimum (4GB just for Docker) would leave under 4GB for Windows, Docker Desktop, VS Code, and a browser simultaneously — a realistic path to constant container restarts. LocalExecutor is a legitimate executor choice for single-node deployments, not a toy substitute, and cut the persistent container count from 7 to 5.

## ADR 3 — dbt dev/prod targets via BigQuery datasets, not separate GCP projects
**Context:** Production systems separate dev and prod with different projects, different Airflow instances, and CI/CD promotion between them.
**Decision:** Implemented only the dbt `target` layer — `dev` and `prod` point at different datasets (`tfl_dev`, `tfl_prod`) inside the same GCP project.
**Reasoning:** Full environment separation would add weeks of infrastructure work with no portfolio value beyond demonstrating the concept, which the lightweight version already does. Documented here so the gap is explicit, not accidental.

## ADR 4 — TfL Line Status as first data source
**Context:** TfL's API exposes many endpoints; Line Status and Arrivals were the two considered for this project.
**Decision:** Built the full pipeline against Line Status first; Arrivals deferred as a second source once the pattern is proven.
**Reasoning:** Smaller, flatter payload — fewer places for a first end-to-end build to break. Debugging pipeline logic and debugging a complex nested response shouldn't happen at the same time.

## ADR 5 — Row-per-(line, status), not row-per-line
**Context:** The TfL API can return more than one concurrent status for a single line (e.g. 'Part Suspended' and 'Special Service' both active at once) — confirmed via real API research, not assumption.
**Decision:** `transform_records()` iterates every entry in each line's `lineStatuses` array, producing one row per status, not just the first.
**Reasoning:** Most example code online naively takes index [0] and silently drops the second status. Preserving both was a deliberate data-quality choice, made before it became a bug report.

## ADR 6 — dense_rank() over row_number() in mart_current_line_status
**Context:** Needed the "latest status per line" logic to respect ADR 5.
**Decision:** Used `dense_rank()` partitioned by line, ordered by `ingested_at` descending, rather than `row_number()`.
**Reasoning:** `row_number()` forces exactly one row per line, arbitrarily picking one if two rows share the same latest timestamp — silently reintroducing the exact data loss ADR 5 was written to prevent. `dense_rank()` keeps every row tied for most recent.

## ADR 7 — Raw zone is append-only (WRITE_APPEND)
**Context:** Every ingest run could either overwrite or accumulate.
**Decision:** `write_to_bigquery()` always appends; the raw table is never truncated.
**Reasoning:** Same audit-trail philosophy as the AWS project's raw zone — an unmodified, timestamped history of exactly what TfL reported and when, regardless of what later transformations do with it.

## ADR 8 — Application Default Credentials over hardcoded key paths
**Context:** The ingest script needs to authenticate to BigQuery, both running locally now and inside an Airflow container later.
**Decision:** Set `GOOGLE_APPLICATION_CREDENTIALS` as an environment variable rather than writing explicit credential-loading code with a hardcoded path.
**Reasoning:** Moving this script into Docker later only requires changing where the env var points — a config change, not a code change. Keeps infrastructure concerns out of application logic, same principle as IAM roles living in CloudFormation rather than hardcoded into Glue
scripts on the AWS project.