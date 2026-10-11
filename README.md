# PortWatch

PortWatch is a $0, local-first data engineering portfolio project for the Port of Los Angeles. Step 00 establishes the Python CLI, typed configuration, local PostGIS service, and project contracts. The historical demo does not require a live AIS key.

## Local setup

Use Python 3.11 (the version installed on this machine). `uv` was installed into an ignored project-local bootstrap environment. From this directory on the current machine:

```powershell
.\.bootstrap-venv\Scripts\uv.exe sync --locked --extra dev
docker compose up -d db
.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch doctor
.\.bootstrap-venv\Scripts\uv.exe run ruff check .
.\.bootstrap-venv\Scripts\uv.exe run pytest tests/test_settings_cli.py
```

On another machine without `uv`, create the same local bootstrap environment with `py -3.11 -m venv .bootstrap-venv` and `.\.bootstrap-venv\Scripts\python.exe -m pip install uv`. If `uv` is already on PATH, the equivalent commands are `uv sync --locked --extra dev` and `uv run ...`. Copy `.env.example` to `.env` only when local overrides are needed; the checked-in defaults work for the localhost demo.

Docker Desktop must be running for the database checks. Docker publishes PostgreSQL only on `127.0.0.1:5432`; the database uses a named local volume. The sample password is strictly for this localhost demo and should be changed if the service is exposed elsewhere. Update `PORTWATCH_DB_PASSWORD` in `.env` before first Compose startup if you change it. Compose initialization variables do not reset a populated volume.

The port bounding box and geofence are intentionally `null` until Step 01 verifies them. `doctor` validates this state and reports database connectivity and PostGIS separately. It exits nonzero if a required check fails.

Project decisions and the current state are in [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/HANDOFF.md](docs/HANDOFF.md).
Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [GitHub workflow](docs/GITHUB_WORKFLOW.md).
