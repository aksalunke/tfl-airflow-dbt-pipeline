# Data Notes — Debugging Journal

## Entry 1 — dbt_project.yml created in the wrong directory
A `cd` command didn't take effect before the next command ran, so `dbt_project.yml` was created at the repo root instead of `dbt\tfl_pipeline\`. `dbt debug` ran successfully anyway — it just found
and used the wrong file, which only became visible by checking the "Using dbt_project.yml file at..." line in its own output. Found the duplicate via `Get-ChildItem -Recurse -Filter dbt_project.yml`, fixed with `Move-Item -Force` into the correct location. 
Lesson: verify a config file landed where intended, don't just trust that a command
succeeded.

## Entry 2 — Conda silently overriding Python resolution
Anaconda was installed and auto-activating the `aws-financial-data-lake` conda environment in every new terminal, which is why `python` returned "not found" in a fresh admin PowerShell window earlier — conda wasn't initialised there, so nothing was on PATH at all. Later, activating `.venv` didn't work as expected because conda's auto-activation ran *after* it, silently overriding it. Fixed with an explicit `conda deactivate` before activating `.venv`, and verified the correct
interpreter with `where.exe python`.

## Entry 3 — TfL API key not visible after registration
Registering on the TfL API portal doesn't surface an API key by itself — a "Products" subscription step is required first, which isn't obvious from the registration flow. Key becomes visible under Profile only after subscribing to a product plan.

## Entry 4 — WSL2 memory ceiling forced an executor change
Default Airflow docker-compose.yaml (CeleryExecutor, 7 containers) targets a documented minimum of 4GB just for Docker, ideally 8GB. Host machine has 7.7GB total RAM. Rather than attempt the default and debug container restarts under memory pressure, pivoted to LocalExecutor
before first run — see ADR 2.

## Entry 5 — FERNET_KEY undocumented on the setup guide, required in the actual file
The official Airflow docs page describing docker-compose setup doesn't mention FERNET_KEY at all. The actual docker-compose.yaml file (fetched directly rather than assumed) requires
`AIRFLOW__CORE__FERNET_KEY: ${FERNET_KEY}`. Left unset, this risks a new random key generating on every container restart, breaking any previously encrypted connection credentials. Generated one explicitly via the `cryptography` package and persisted it in `.env`.

## Entry 6 — dbt source freshness config deprecation, two rounds
First `dbt run` succeeded but warned that `freshness` needed to nest under a new `config:` key (schema change in dbt 1.9+, not mentioned in older tutorials). Fixed by nesting it — but the fix introduced a second warning: `columns` had also ended up nested inside `config` by mistake,
which dbt flagged as an unrecognized custom key. Root cause was a manual indentation error while adding the new nesting level by hand. Fixed by rewriting the file cleanly rather than patching indentation piecemeal.

## Entry 7 — GOOGLE_APPLICATION_CREDENTIALS missing despite being instructed
The env var was specified as a setup step, but verification later revealed it was never actually added to `.env` — a step that looked complete based on conversation flow wasn't actually done on disk. Caught by directly checking file contents rather than assuming prior instructions were followed. 
Lesson: verify state, don't infer it from conversation history.