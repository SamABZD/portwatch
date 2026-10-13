# Contributing to PortWatch

PortWatch is a five-day, $0 academy project. Work in the dedicated PortWatch repository and follow the supplied stage prompts in order. Read [the working agreement](docs/WORKING_AGREEMENT.md), [decisions](docs/DECISIONS.md), and [handoff](docs/HANDOFF.md) before each stage.

## Local checks on Windows

From the repository root, use Python 3.11 and the project-local `uv` environment documented in the README. If it does not exist yet:

```powershell
py -3.11 -m venv .bootstrap-venv
.\.bootstrap-venv\Scripts\python.exe -m pip install uv
```

Run the shared local/CI gate:

```powershell
.\.bootstrap-venv\Scripts\uv.exe run --locked --extra dev python scripts/validate.py
```

Use `--group quality` if PostgreSQL is unavailable; this does not establish an integration pass. Integration requires PostgreSQL 17/PostGIS 3.6 and uses isolated synthetic schemas. Windows can use the existing native runtime without Docker. See [GitHub workflow](docs/GITHUB_WORKFLOW.md) for command groups and test policy. Run `python -m portwatch doctor` separately to check operational settings and optional live credentials; CI deliberately does not require an AIS key.

## Branches, commits, and pull requests

Start a feature branch from the latest `main`, for example `feat/historical-ingestion` or `feat/map-replay`. Keep a branch scoped to one prompt. Commit working changes with a short descriptive subject and explain the reason in the body when it is not obvious. Do not add artificial or backdated commits. Include the stage scope, exact checks and outcomes, data or schema impact, and remaining blockers in a pull request.

Review `git status --short`, `git diff --check`, and the staged file list before committing. Stage explicit project paths. Keep `.env`, credentials, downloaded data, local database files, and generated artifacts out of Git; small labeled synthetic fixtures are welcome under `tests/fixtures/`.

If a local artifact is staged accidentally, run `git restore --staged -- <path>`, add a suitable ignore rule, and verify `git status` again. If it was committed but not pushed, remove it from tracking with `git rm --cached -- <path>` and make a correction commit. If a secret reaches a published commit, revoke or rotate it immediately, remove it from current tracking, and coordinate any history cleanup; do not casually force-push or assume deletion in a later commit erases exposure.

See [the GitHub workflow](docs/GITHUB_WORKFLOW.md) for the full routine. Require successful `quality` and `integration` checks before merging subsequent PRs.
