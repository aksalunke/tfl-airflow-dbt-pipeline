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

---

## What's Not Yet Built
The DAG itself — Airflow calling the ingest script and dbt commands
automatically, in sequence, on a schedule. Everything above is proven
working independently; wiring it together is the next phase.

