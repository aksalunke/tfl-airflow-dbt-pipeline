# Setup Guide — Rebuilding This Project From Scratch

This is the complete, ordered sequence to stand up this project's local environment on a fresh Windows 10 machine. Reasoning behind specific choices lives in `architecture-decisions.md`; bugs hit along the way live in `data-notes.md`. This file is the runbook — what to actually run.

Tested on: Windows 10, 7.7GB RAM, Python 3.13.14, dbt 1.12.0, Airflow 3.3.0, Docker Desktop with WSL 2 backend.

---

## Phase 1 — Local Machine Prerequisites
**Python 3.13** — install from python.org (not the Microsoft Store alias). During install, tick "Add python.exe to PATH". Avoid 3.14 — a known dbt-core issue at time of writing (see ADR 1).

**WSL 2** — in an elevated (Admin) PowerShell:
```powershell
wsl --install
```
Restart when prompted. On first boot, set a simple Ubuntu username/password.

**Docker Desktop** — download from docker.com, AMD64 build. During setup, select "Use WSL 2 instead of Hyper-V". Skip sign-in when prompted.

**WSL 2 memory limit** — create `C:\Users\<you>\.wslconfig`:
```ini
[wsl2]
memory=4GB
```
Then: `wsl --shutdown`, and restart Docker Desktop. (See ADR 2 for why 4GB, not the default 8GB the official Airflow docs recommend.)

---

## Phase 2 — External Accounts and Credentials
**TfL API key** — register at api-portal.tfl.gov.uk. Registration alone does not surface a key — go to **Products**, subscribe to a plan, then the key appears under **Profile**.

**GCP project** — create a project at console.cloud.google.com (e.g. `dev-tfl-pipeline`). Note the exact **Project ID** shown in the console, not just the display name. Visit BigQuery once to auto-activate the free sandbox tier — no billing needed.

**Service account for dbt** — in IAM & Admin → Service Accounts, create `dbt-bigquery-sa` with roles **BigQuery Data Editor** and **BigQuery Job User**. Generate a JSON key, save it as
`keys/service-account.json` in the repo (this folder is gitignored).

---

## Phase 3 — Repo and Secrets Scaffolding
```powershell
mkdir dags, ingestion, tests, docs
mkdir dbt\tfl_pipeline\models\staging
mkdir dbt\tfl_pipeline\models\mart
```

**`.env`** at repo root (never committed):
TFL_API_KEY=<your key>
GCP_PROJECT_ID=<your project id>
AIRFLOW_UID=50000
FERNET_KEY=<generated — see Phase 5>
GOOGLE_APPLICATION_CREDENTIALS=<absolute Windows path to keys\service-account.json>

**`.gitignore`** additions:
.env
.venv/
pycache/
*.pyc
dbt/tfl_pipeline/target/
dbt/tfl_pipeline/dbt_packages/
logs/
keys/
config/

**Windows persistent env var** (needed for local `dbt` CLI, separate from
`.env` since dbt doesn't read `.env` itself):
```powershell
[System.Environment]::SetEnvironmentVariable("GOOGLE_APPLICATION_CREDENTIALS", "<absolute path to keys\service-account.json>", "User")
```
Requires a fresh terminal to take effect.

---

## Phase 4 — Python Environment and dbt
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
If Anaconda auto-activates a different environment, run `conda deactivate`
first (see `data-notes.md`, Entry 2).

```powershell
pip install dbt-bigquery
pip install requests google-cloud-bigquery python-dotenv
```

**`~/.dbt/profiles.yml`** (outside the repo, by dbt's own design):
```yaml
tfl_pipeline:
  target: dev
  outputs:
    dev:
      type: bigquery
      method: oauth
      project: <your GCP project ID>
      dataset: tfl_dev
      threads: 4
      timeout_seconds: 300
      location: EU
      priority: interactive
```
`method: oauth` relies on Application Default Credentials — the same `GOOGLE_APPLICATION_CREDENTIALS` variable set above, no hardcoded keyfile
path (see ADR 8).

**`dbt/tfl_pipeline/dbt_project.yml`** — see repo for full content;
key setting: staging models materialize as views, mart models as tables.

Verify:
```powershell
cd dbt\tfl_pipeline
dbt debug
```
Expect `Connection test: [OK connection ok]`.

---

## Phase 5 — Airflow (Docker, LocalExecutor)
From the repo root:
```powershell
curl.exe -LfO 'https://airflow.apache.org/docs/apache-airflow/3.3.0/docker-compose.yaml'
```

Edit it for LocalExecutor (removes Redis + separate worker — see ADR 2):
- Change `AIRFLOW__CORE__EXECUTOR: CeleryExecutor` → `LocalExecutor`
- Delete the two `AIRFLOW__CELERY__*` env lines
- Remove `redis:` from the `depends_on` anchor block
- Delete the entire `redis:` service block
- Delete the entire `airflow-worker:` service block

Generate a Fernet key and add it to `.env`:
```powershell
python -m pip install cryptography --quiet
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create supporting folders:
```powershell
mkdir logs, plugins, config
```

---

## Phase 6 — Custom Airflow Image (dbt + ingest script inside the container)
**`Dockerfile`** at repo root:
```dockerfile
FROM apache/airflow:3.3.0

COPY ingestion/requirements.txt /tmp/ingestion-requirements.txt

RUN pip install --no-cache-dir -r /tmp/ingestion-requirements.txt \
    && pip install --no-cache-dir dbt-bigquery==1.12.0
```

**`docker-compose.yaml`** additions to `x-airflow-common`:

Replace the stock image line with:
```yaml
  build:
    context: .
    dockerfile: Dockerfile
  image: tfl-airflow-dbt-pipeline:latest
```

Add to the `environment:` block:
```yaml
    GOOGLE_APPLICATION_CREDENTIALS: /opt/airflow/keys/service-account.json
    DBT_PROFILES_DIR: /opt/airflow/dbt_profile
    DBT_PROJECT_DIR: /opt/airflow/dbt/tfl_pipeline
```

Add to the `volumes:` list:
```yaml
    - ./ingestion:/opt/airflow/ingestion
    - ./dbt:/opt/airflow/dbt
    - ./keys:/opt/airflow/keys:ro
    - C:/Users/<you>/.dbt:/opt/airflow/dbt_profile:ro
```

Build and start:
```powershell
docker compose down
docker compose build
docker compose up airflow-init
docker compose up -d
docker ps
```
Expect 5 healthy containers: postgres, airflow-scheduler, airflow-apiserver,
airflow-dag-processor, airflow-triggerer.

Verify dbt works *inside* the container:
```powershell
docker compose exec airflow-scheduler dbt debug --project-dir /opt/airflow/dbt/tfl_pipeline
```

---

## Phase 7 — Verify the Full Data Layer
```powershell
python ingestion\tfl_ingest.py
```
Check `tfl_dev.raw_line_status` in the BigQuery console.

```powershell
cd dbt\tfl_pipeline
dbt run --select staging
dbt test --select staging
dbt run --select mart
dbt test --select mart
```
All tests should pass. Check `tfl_dev.mart_current_line_status` in BigQuery.

## Phase 8 — The DAG (Orchestration)
`dags/tfl_pipeline_dag.py` wires the ingest script and dbt commands into one dependency chain: ingest → dbt run staging → dbt test staging → dbt run mart → dbt test mart. 
Any task failing stops everything downstream of it.

Key settings: 
`catchup=False` (TfL's API only answers "right now" — backfilling past dates would just relabel today's data as historical, see ADR 10), 
`retries=3` with a 5-minute delay, 
schedule `"0 6 * * *"`.

Airflow 3.x import paths, confirmed against current docs — not the older 2.x paths most tutorials still show: 
```python
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator
```

The dag-processor picks up new/changed files automatically — allow up to 5 minutes, or just refresh the UI. New DAGs load paused by default; trigger manually first to prove it works, then unpause (or tick "Unpause on trigger" in the trigger dialog to do both at once).

Verify: trigger via `http://localhost:8080`, watch the Graph view — all 5 tasks should go green. Cross-check the `ingested_at` timestamp against BigQuery `raw_line_status` to confirm Airflow (not a manual run) actually wrote it — remember the UI shows local browser time, BigQuery stores UTC.

---

## Phase 9 — Local Dev Tooling: pytest, ruff, mypy
```powershell
pip install pytest ruff mypy
```

Capture exact versions into `requirements-dev.txt`:
```powershell
pip freeze | Select-String "pytest|ruff|mypy"
```

**`pyproject.toml`** additions:
```toml
[project]
name = "tfl-airflow-dbt-pipeline"
version = "0.1.0"
requires-python = ">=3.13"

[tool.pytest.ini_options]
pythonpath = ["ingestion"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.13"
ignore_missing_imports = true
warn_unused_ignores = true
```

`tests/test_tfl_ingest.py` covers `transform_records` (including the concurrent-status edge case from ADR 5), 
`_require_env`, `fetch_line_status` (mocked via `@patch`, since it calls `requests.get` internally), and 
`ensure_dataset_exists` (dependency-injected mock client, no patching needed — see ADR 8's design pattern).

Verify:
```powershell
ruff check .
mypy ingestion\tfl_ingest.py
pytest -v
```
Expect 9 passed, zero lint/type issues.

---

## Phase 10 — GitHub Actions CI
`.github/workflows/ci.yml` — triggers on push and PR to `main`, runs on a fresh `ubuntu-latest` runner: checkout, Python 3.13 setup, install both requirements files, then `ruff check .`, `mypy`, `pytest -v` in sequence.

Original scope was Python-only (ADR 9) — extended to cover dbt and DAG syntax in Phase 11 below (ADR 15).

Verify: push, check the **Actions** tab on GitHub — green check within under a minute confirms the whole chain works on a machine that's never seen this code before, not just locally.

---

## Phase 11 — Extending CI to dbt Validation and DAG Syntax
Add a repository secret: **Settings → Secrets and variables → Actions → New repository secret**, name `GCP_SA_KEY`, value = the full raw contents of `keys/service-account.json`.

**`.dbt-ci/profiles.yml`** (committed — no secrets, `method: oauth`):
```yaml
tfl_pipeline:
  target: ci
  outputs:
    ci:
      type: bigquery
      method: oauth
      project: dev-tfl-pipeline
      dataset: tfl_ci
      threads: 4
      timeout_seconds: 300
      location: EU
      priority: interactive
```

`_tfl__sources.yml` hardcodes `schema: tfl_dev`, so CI reads real raw data regardless of target — only the *build* destination is isolated. See ADR 15.

Add to `.github/workflows/ci.yml`:
```yaml
      - name: Check DAG file compiles
        run: python -m py_compile dags/tfl_pipeline_dag.py

      - name: Install dbt
        run: pip install dbt-bigquery==1.12.0

      - name: Write GCP service account key
        run: echo '${{ secrets.GCP_SA_KEY }}' > /tmp/gcp-key.json

      - name: dbt run and test
        env:
          GOOGLE_APPLICATION_CREDENTIALS: /tmp/gcp-key.json
          DBT_PROFILES_DIR: ${{ github.workspace }}/.dbt-ci
          DBT_PROJECT_DIR: ${{ github.workspace }}/dbt/tfl_pipeline
        run: |
          dbt run
          dbt test
```

DAG check is `py_compile` only — syntax, not import validity. A full `apache-airflow` install was considered and rejected as disproportionate for this check (ADR 15).

Verify: push, check **Actions** — green within ~90 seconds.

---

## What's Left

Nothing infrastructure-related — this runbook reflects the full, current, CI-checked state of the repo.



