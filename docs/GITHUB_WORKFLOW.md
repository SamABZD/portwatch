# GitHub workflow

## Repository boundary

This repository contains only PortWatch. Its root is the `portwatch` directory, not the parent `dataEngineering` folder. `main` is the default branch. `origin` must identify only `SamABZD/portwatch`; verify it with `git remote -v` before pushing. The public [Flux repository](https://github.com/SamABZD/flux) is a read-only organization reference. Its text-normalization pattern informed `.gitattributes`; Step 05A adapts its [CI pattern](https://github.com/SamABZD/flux/blob/main/.github/workflows/ci.yml) for Python and PostGIS.

## Normal change routine

1. Read the assigned prompt, project brief, working agreement, decisions, handoff, and affected code. Inspect `git status --short --branch` and `git remote -v`.
2. Update `main` normally, then make a scoped branch such as `feat/historical-ingestion` or `feat/map-replay`. Do one stage at a time.
3. Implement the stage, run relevant local checks from `CONTRIBUTING.md`, and record exact commands and observed results. A missing Docker service is a blocker, not a test pass.
4. Update `docs/DECISIONS.md` for new choices and `docs/HANDOFF.md` for stage state, blockers, data locations, and the next step.
5. Inspect `git status --short`, stage explicit paths, inspect `git diff --cached --check` and `git diff --cached --stat`, and confirm the staged list contains no private or bulk data. Make a meaningful commit. Do not manufacture development history.
6. Push the branch and open a pull request to `main` with the stage scope, validation evidence, data/schema impact, and blockers. Review the diff before merging. Keep commits scoped and avoid force-pushing published history merely to tidy it.

Use `git check-ignore -v <path>` to confirm an artifact is excluded. To unstage an accidental file, use `git restore --staged -- <path>`. If already committed, remove it from tracking in a new correction commit. For a published secret, revoke or rotate it first, then coordinate repository cleanup; history rewriting alone does not revoke a credential.

## Verification gates

From the repository root, run the same entry point used by GitHub:

```powershell
.\.bootstrap-venv\Scripts\uv.exe run --locked --extra dev python scripts/validate.py
```

With `uv` on PATH on Windows, Linux, or macOS:

```sh
uv run --locked --extra dev python scripts/validate.py
```

Use `--group quality` for lint, format, unit tests, and package checks without a database. Use `--group integration` for PostgreSQL 17/PostGIS 3.6 checks. The default runs both. Integration uses local database settings from the ignored `.env` or environment overrides; the native Windows database requires no Docker download. Each integration fixture creates and drops only its own temporary schema. Application tables and saved provider data are preserved.

The validator runs `uv sync --locked --extra dev`, Ruff lint and format checks, non-integration/integration pytest selections, and `uv build`. It imports the actual wheel in isolated Python, checks its SQL migration resources and CLI help, and checks fresh/repeated migrations. Empty, skipped, failed, or errored test suites fail validation. New tests with the `integration` marker join the database gate automatically; unmarked tests join the unit gate.

Tests use small labeled synthetic data, loopback HTTP/WebSocket servers, and temporary output roots. A shared fixture rejects external Python socket lookups/connections. The validator blanks the AIS key in subprocesses and never runs provider ingestion, weather/news calls, or historical downloads. Dependency installation and package building may use the network.

## GitHub Validate jobs

`.github/workflows/ci.yml` runs on branch pushes, PRs targeting `main`, and manual dispatch. Distinct job names are `quality` and `integration`, each with an Ubuntu 24.04 runner and 15-minute timeout. Concurrency cancels obsolete runs for the same branch/PR. Permissions default to `contents: read`; checkout does not persist credentials. No provider secrets, failure artifact uploads, workspace caches, or scheduled AIS collectors are configured.

The repository is public and uses a standard hosted runner, whose execution is [free under GitHub's billing policy](https://docs.github.com/en/actions/concepts/billing-and-usage). No paid runner, deployment service, or artifact upload is requested.

Official action versions verified on 2026-10-11:

- [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- [astral-sh/setup-uv v10.3.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.0), pinned to `1c37ad07a6a961277cf70c0d37d6f313000f5884`.

Both jobs use uv 0.13.0, Python 3.11, locked development dependencies, and only the uv dependency cache keyed by `uv.lock`. Integration starts [postgis/postgis:17-3.6-alpine](https://github.com/postgis/docker-postgis), checks readiness with `pg_isready`, and uses disposable CI-only credentials. The image tag matches the native installation's PostgreSQL/PostGIS major versions; patch image versions may change. The validator checks both server versions explicitly. Each hosted job gets a fresh database service.

Remote run, PR, and branch-rule evidence is recorded in [GITHUB_SETUP.md](GITHUB_SETUP.md) and [HANDOFF.md](HANDOFF.md). Reading YAML or passing local tests alone does not establish a GitHub pass.

Current Step05A status: CI PR#2 is open; account billing lock prevented both hosted jobs from starting. Required check settings are proposed but unapplied until an actual baseline succeeds. The account owner must clear that lock before PR/main execution can be verified.

On 2026-10-12 the user declined paying for the account lock and authorized continuing locally.
Step06 is a local checkpoint on `feat/geospatial-zones`, based on pending CI head `35b230c`.
Run the shared local validation command for each stage and preserve CI as a separate pending gate.
No unchecked merge, paid runner, plan/payment change, or deployment is authorized by this deferral.

Step07 is also complete locally on `feat/visits-waiting`, based on Step06 checkpoint `a068ef6`.
Both local stages remain unpushed; CI PR#2 and main remain unchanged. Shared local validation
passes 109 unit/22 integration tests with four migrations. This evidence does not establish a
remote GitHub execution pass.

Step08 context connectors are complete locally on `feat/weather-news`, based on Step07
`ac684739f794997d67a63a9a97d403a99c1c1477`. Shared validation passes134 unit/32 integration
tests and five migrations. Steps06–08 remain unpushed; the existing CI deferral persists.
Real GDELT429 is a provider availability limit, separate from the GitHub account lock.

Step09 analytical tables are complete locally on `feat/analytics-marts`, based on Step08 `8dd89ba`.
Shared validation passes139 unit/44 integration tests, six migrations and the packaged hourly SQL
resource. Steps06–09 remain unpushed; no remote check, protection change or CI merge is claimed.
