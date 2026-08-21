# Architecture Decisions — TfL Airflow + dbt Pipeline

## ADR 1 — Python 3.13 over 3.14
**Context:** 3.14 was the newest available version at setup time. 

**Decision:** Installed 3.13 instead.

**Reasoning:** Airflow 3.2+ supports both, but a dbt-core GitHub issue (Oct 2025) documented 3.14 crashing on a mashumaro serialisation bug, with a fix only milestoned for a future release. The BigQuery adapter's 3.14 compatibility wasn't confirmed either. Chose the version with confirmed
support across the full stack over the newest available.

## ADR 2 — LocalExecutor over CeleryExecutor
**Context:** Airflow's official docker-compose.yaml defaults to CeleryExecutor — 7 containers including Redis and a separate worker.

**Decision:** Removed Redis and the worker service; switched to LocalExecutor, where the scheduler runs tasks as local subprocesses. 

**Reasoning:** Host machine has 7.7GB total RAM. CeleryExecutor's
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

**Reasoning:** Most example code online naively takes index [0] and silently drops the second status. Preserving both was a deliberate data-quality choice, made before it became a bug report. Covered by 'test_transform_records_preserves_concurrent_statuses' in the pytest suite — the design decision is now enforced, not just documented.

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

## ADR 9 — CI scope limited to ingestion/ Python code
**Context:** GitHub Actions CI runs lint, type-check, and tests on every push/PR to main.

**Decision:** CI covers only `ingestion/` (ruff, mypy, pytest) — dbt models and the Airflow DAG are not validated automatically.

**Reasoning:** Testing dbt in CI needs BigQuery credentials as a GitHub Secret and a separate CI-only dataset, so automated runs never pollute `tfl_dev`. Testing the DAG needs Docker running inside the CI runner itself. Both are real, addressable gaps — deliberately scoped out to
ship a working CI pass first, not oversights.

**Update:** superseded by ADR 15 — dbt and DAG syntax checks were added to CI.

## ADR 10 — catchup=False, no historical backfill
**Context:** `start_date` is necessarily in the past by the time the DAG is first deployed and unpaused.

**Decision:** `catchup=False` — Airflow does not backfill missed scheduled runs between `start_date` and today.

**Reasoning:** TfL's Line Status endpoint only ever answers "what's happening right now" — there's no way to ask it about a past date. A backfilled run would just call the live endpoint and relabel today's snapshot as historical, which is misleading rather than a genuine gap-fill. `catchup=True` is correct for sources with real historical records (a warehouse table already holding past data) — not this one.

## ADR 11 — dbt authentication via oauth/ADC, extending ADR 8 to dbt 
**Context:** `profiles.yml` originally authenticated via `method: service-account` with an explicit Windows file path — unusable once dbt needed to run inside a Linux container too.

**Decision:** Switched to `method: oauth`, which resolves credentials via Application Default Credentials (`google.auth.default()`) — the same mechanism the ingest script already used (ADR 8).

**Reasoning:** Verified directly against dbt-bigquery's actual source behavior rather than assumed — `method: oauth` genuinely calls `google.auth.default()`, checking `GOOGLE_APPLICATION_CREDENTIALS` first. One `profiles.yml` now works unchanged on Windows and inside Docker; only the environment variable's *value* differs — set as a persistent Windows user variable locally, overridden per-container in `docker-compose.yaml`.

## ADR 12 — BashOperator for every DAG task, including the ingest script
**Context:** The ingest task could have used PythonOperator (importing `tfl_ingest.main()` directly) or BashOperator (shelling out to `python tfl_ingest.py`).

**Decision:** BashOperator throughout.

**Reasoning:** Every task's `bash_command` is identical to the command already used for manual testing during development. If a task fails, the exact same command can be copied and run directly inside the container to reproduce it — no translation between "how Airflow calls
it" and "how I'd call it myself."

## ADR 13 — Scheduler reliability is bound to host machine uptime
**Context:** This DAG's scheduler runs inside Docker Desktop on a personal laptop, not a cloud-hosted Airflow instance.

**Decision:** Accepted as a scoped, documented limitation.

**Reasoning:** If the host machine is asleep or Docker Desktop isn't running at 6am, that day's run simply doesn't happen, and `catchup=False` (ADR 10) means it's never backfilled. A managed
deployment (e.g. Cloud Composer) would remove this dependency entirely.

## ADR 14 — Dependency injection over internal construction, for testability
**Context:** Writing pytest tests surfaced a real difference in how easily two similarly-shaped functions could be tested.

**Decision:** `ensure_dataset_exists(client, ...)` accepts its BigQuery client as a parameter rather than constructing one internally, unlike `fetch_line_status`, which calls `requests.get` directly inside itself.

**Reasoning:** The dependency-injected function needed only a plain `MagicMock()` passed in to test. `fetch_line_status` required `@patch("tfl_ingest.requests.get")` to forcibly substitute its internal call. Where a dependency can reasonably be passed in rather than self-constructed, doing so avoids needing to patch at all — worth applying deliberately to future functions, not just noticed after the fact.

## ADR 15 — Extended CI to cover dbt and DAG syntax, superseding ADR 9
**Context:** ADR 9 scoped CI to Python only, citing the cost of BigQuery credentials in CI and a CI-specific dataset.

**Decision:** Both gaps closed. A `GCP_SA_KEY` GitHub Secret plus a CI-only dbt target (`.dbt-ci/profiles.yml`, oauth/ADC, `dataset: tfl_ci`) now runs `dbt run` and `dbt test` in CI. A
`python -m py_compile` step also validates the DAG file's syntax.

**Reasoning:** Since `_tfl__sources.yml` hardcodes `schema: tfl_dev`, CI reads real raw data (populated by the daily DAG) but writes all built models into the isolated `tfl_ci` dataset — CI can never overwrite `tfl_dev.mart_current_line_status`. The DAG check remains lightweight (`py_compile`, not a full Airflow install) — it validates syntax, not that `airflow.sdk` and `airflow.providers.standard` still resolve to real packages; a genuine, accepted gap given the cost of pinning a full Airflow install via constraints files just for CI.