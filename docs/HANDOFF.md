# Handoff

## Stage state

- Completed: Step 00 foundation implementation, resolved lockfile, package install, CLI, typed settings, Los Angeles config, and documentation. The local database check remains blocked by missing Docker.
- Next: Step 00A GitHub setup, then Step 01 source feasibility audit. Do not execute either as part of Step 00.

## Verified commands and observed outcomes

- `python --version`: Python 3.11.9. `py -0p`: Python 3.14 and 3.11 installed, no 3.12. `py -3.14 --version`: Python 3.14.0. `git --version`: Git 2.53.0.windows.2. `uv --version` and `docker --version`: commands unavailable on the original PATH; Docker Compose is unavailable with Docker.
- `git rev-parse --show-toplevel` from `C:\Users\samab\dataEngineering`: failed because the parent is not a Git repository. No repository or remote was created in Step 00.
- `py -3.11 -m venv .bootstrap-venv` followed by `.\.bootstrap-venv\Scripts\python.exe -m pip install uv`: succeeded; project-local uv 0.13.0. The bootstrap environment is ignored by Git.
- `.\.bootstrap-venv\Scripts\uv.exe sync --extra dev`: succeeded, resolved and installed 25 packages using Python 3.11.9, and wrote `uv.lock`.
- `.\.bootstrap-venv\Scripts\uv.exe sync --locked --extra dev`: succeeded, checked the same 25 packages without changing the lockfile.
- `.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch --help`: succeeded and lists `doctor`.
- `.\.bootstrap-venv\Scripts\uv.exe run ruff check .`: passed after fixing line-length findings.
- `.\.bootstrap-venv\Scripts\uv.exe run pytest tests/test_settings_cli.py`: 5 passed.
- `.\.bootstrap-venv\Scripts\uv.exe run python -c 'import yaml; from pathlib import Path; c=yaml.safe_load(Path("compose.yaml").read_text()); assert c["services"]["db"]["image"] == "postgis/postgis:16-3.5"; assert c["services"]["db"]["ports"][0].startswith("127.0.0.1:"); assert c["services"]["db"]["healthcheck"]; print("Compose YAML structure: PASS")'`: passed. This validates the file structure, not a running service.
- `docker compose up -d db`: failed because `docker` is not installed/on PATH; no container was started.
- `.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch doctor`: Python, package, settings, port config, and raw-directory checks passed. Database failed with `OperationalError`; PostGIS was skipped because no database was reachable. Live AIS key was reported optional and missing. Overall exit code 1 is expected until Docker runs.

## Pending problems and user actions

- Install and start Docker Desktop for Windows using the [official Windows installation guide](https://docs.docker.com/desktop/setup/install/windows-install/). Its WSL 2 backend requires the documented Windows, WSL, virtualization, and memory prerequisites. Docker's [license terms](https://docs.docker.com/subscription-billing/desktop-license/) list education and personal use as free; check that your use qualifies.
- Then, in `C:\Users\samab\dataEngineering\portwatch`, run `docker --version`, `docker compose version`, `docker compose up -d db`, and `.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch doctor`. A fully healthy doctor should show `PASS` for Database and PostGIS, and `Overall: PASS`. If the database does not start, inspect `docker compose ps` and `docker compose logs db`; do not erase the named volume to troubleshoot without preserving its data.
- Step 01 must verify a Los Angeles bounding box and geofence source. They are intentionally `null` now; the 1-knot, 30-minute, and 15-minute thresholds are provisional.

## Data locations

- `data/raw/`: original downloaded provider files and responses, ignored by Git.
- `data/derived/`: transformed local datasets, ignored by Git.
- `data/quarantine/`: rejected payloads, ignored by Git.
- `tests/fixtures/`: small synthetic test fixtures, trackable.
- Docker named volume `portwatch_pgdata`: local PostGIS files when Docker is available.

No provider data has been downloaded in Step 00.
