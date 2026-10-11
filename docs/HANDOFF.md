# Handoff

## Stage state

- Completed: Step 00 foundation implementation and Step 00A GitHub setup. The local database check from Step 00 remains blocked by missing Docker.
- Published repository: [SamABZD/portwatch](https://github.com/SamABZD/portwatch), public, default branch `main`, only remote `origin`. See [GITHUB_SETUP.md](GITHUB_SETUP.md) for the recorded account, foundation commit SHA, metadata, and publication checks.
- Next: Step 01 source feasibility audit. Step 05A adds GitHub CI later. Do not start Step 01 within Step 00A.

## Verified commands and observed outcomes

### Step 00A

- `gh --version`: 2.97.0. `gh auth status` showed active account `SamABZD`; `gh api user --jq .login` returned `SamABZD`. `gh repo view SamABZD/portwatch --json name,owner,isPrivate,defaultBranchRef,url` failed before creation because the target repository did not exist.
- `git init -b main`: initialized the independent repository at `C:\Users\samab\dataEngineering\portwatch`. Existing global author name and email were reused in repository-local settings; no global Git settings were changed.
- `git check-ignore -v` on `.env`, both virtual environments, raw/derived/quarantine paths, artifacts, and logs: every tested private or generated path was ignored. `git diff --cached --check`: passed. `git grep --cached -n -I -E 'gho_[A-Za-z0-9]{20}|ghp_[A-Za-z0-9]{20}|sk-[A-Za-z0-9]{20}'`: no matches. `git ls-files --cached --eol` reported LF normalized staged files.
- `git commit -m "Establish PortWatch foundation and GitHub workflow"`: created foundation commit `01c9f0b0c16e256b6c80f092e41e33097becb8bc` with 22 intended project files.
- `gh repo create SamABZD/portwatch --public --source . --remote origin --push`: succeeded and pushed `main`.
- `gh repo edit SamABZD/portwatch --description "Local-first Port of Los Angeles AIS data engineering portfolio project" --add-topic data-engineering,python,postgresql,postgis,ais,portfolio`: succeeded. `gh repo view ... --json nameWithOwner,isPrivate,defaultBranchRef,url,description,repositoryTopics` confirmed public visibility, `main`, description, and six topics.
- `git remote -v`, `git rev-parse HEAD`, and `git ls-remote origin refs/heads/main`: after the first push, `origin` pointed only to `https://github.com/SamABZD/portwatch.git` and both SHAs matched the foundation commit. `gh api repos/SamABZD/portwatch/contents` and the recursive tree endpoint showed the expected files and README; no `.env`, data, virtual environment, or other parent project was present.

### Step 00

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

- Step 01 source auditing can proceed without Docker. Step 02 needs a running PostgreSQL/PostGIS service for migrations and integration checks; the Step 00 doctor database gate remains unverified until then.
- Before Step 02, install and start Docker Desktop for Windows using the [official Windows installation guide](https://docs.docker.com/desktop/setup/install/windows-install/) or provide an equivalent local PostGIS service. The Docker WSL 2 backend requires the documented Windows, WSL, virtualization, and memory prerequisites. Docker's [license terms](https://docs.docker.com/subscription-billing/desktop-license/) list education and personal use as free; check that your use qualifies.
- With Docker running, in `C:\Users\samab\dataEngineering\portwatch`, run `docker --version`, `docker compose version`, `docker compose up -d db`, and `.\.bootstrap-venv\Scripts\uv.exe run python -m portwatch doctor`. A fully healthy doctor should show `PASS` for Database and PostGIS, and `Overall: PASS`. If the database does not start, inspect `docker compose ps` and `docker compose logs db`; do not erase the named volume to troubleshoot without preserving its data.
- Step 01 must verify a Los Angeles bounding box and geofence source. They are intentionally `null` now; the 1-knot, 30-minute, and 15-minute thresholds are provisional.

## Data locations

- `data/raw/`: original downloaded provider files and responses, ignored by Git.
- `data/derived/`: transformed local datasets, ignored by Git.
- `data/quarantine/`: rejected payloads, ignored by Git.
- `tests/fixtures/`: small synthetic test fixtures, trackable.
- Docker named volume `portwatch_pgdata`: local PostGIS files when Docker is available.

No provider data has been downloaded in Step 00.
