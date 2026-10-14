# GitHub workflow

## October 14, 2026 completed release

[PR #9](https://github.com/SamABZD/portwatch/pull/9) merged at `fb89c3c` and the existing
Vercel integration deployed that revision to [production](https://portwatch-iota.vercel.app/).
[Phase 6 evidence](evidence/phase6-release.json) records329 Python tests,14 Node tests,
63 browser checks and separate real-response production verification. Anonymous repository,
README, code and all four screenshot hashes are verified. About/homepage/topics are updated.
[Profile commit99ba386](https://github.com/SamABZD/SamABZD/commit/99ba386936d39aa497b0c738c641b0cb9fbdad13)
adds `03 / PortWatch`; rendered links/image and Flux/Retro/PortWatch pins are verified.

Repository visibility changed externally; the agent did not change it. Actions remain
disabled, Railway deployments/resources and the original v0.1.0 tag are unchanged.
Expected recurring frontend cost delta0 assumes unchanged traffic/backend workload;
actual billing delta remains unavailable. No code license has been selected; non-secret
operational context remains in older reachable commits. Older statuses below describe
their recorded checkpoints and are superseded by this completed release.


## Current owner override (2026-10-13)

The owner explicitly disabled GitHub Actions and requested continuing with health fixes and
publication. Keep repository permissions `enabled:false` and Validate `disabled_manually`.
Local full validation is the release gate, superseding the hosted PR/main gates in the
historical preparation record below. Final implementation passed258 tests and package/
migration checks; Next.js built locally and on Vercel. No GitHub-hosted pass is claimed.
The owner separately authorized deployment on their existing paid Railway workspace with
a Vercel frontend; see [deployment operations](../deploy/README.md).

## Historical preparation record

Step 14A packages the locally verified Step 13 code and presentation on
`release/portfolio-v0.1.0`. Current remote visibility is private, superseding earlier public
records. Keep privacy and account settings unchanged. A current-head hosted validation pass
and resulting-main pass remain required for merge and `v0.1.0`; preparation/push/PR do not
establish a release. See [GITHUB_SETUP.md](GITHUB_SETUP.md) for observed remote evidence.

[Publication PR#3](https://github.com/SamABZD/portwatch/pull/3) is open. Initial publication
push/PR runs completed `startup_failure` with zero jobs/checks and no detailed API annotation.
An empty check list is not a pass; do not merge until quality/integration actually execute
successfully on the intended current head. Earlier jobs did not receive a runner;
the exact cause of the later startup failure is not exposed by the API.

Step13 verified242 tests through the shared local validator, fresh native reproduction and
Next.js build/browser checks. Publication attempts have not executed any hosted tests;
the workflow covers Python quality/integration, while frontend verification was local. Compose matches its PG17/
PostGIS3.6 image, guarded by a regression test. See [RELEASE_CHECK.md](RELEASE_CHECK.md).

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

The repository was public when CI was configured; it is now private. Standard hosted-runner
usage follows the account's available allowance. Earlier hosted runs
failed before any runner started. No paid runner, deployment service, artifact
upload or account/payment change is requested.

Official action versions verified on 2026-10-11:

- [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), pinned to `3d3c42e5aac5ba805825da76410c181273ba90b1`.
- [astral-sh/setup-uv v10.3.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.0), pinned to `1c37ad07a6a961277cf70c0d37d6f313000f5884`.

Both jobs use uv 0.13.0, Python 3.11, locked development dependencies, and only the uv dependency cache keyed by `uv.lock`. Integration starts [postgis/postgis:17-3.6-alpine](https://github.com/postgis/docker-postgis), checks readiness with `pg_isready`, and uses disposable CI-only credentials. The image tag matches the native installation's PostgreSQL/PostGIS major versions; patch image versions may change. The validator checks both server versions explicitly. Each hosted job gets a fresh database service.

Remote run, PR, and branch-rule evidence is recorded in [GITHUB_SETUP.md](GITHUB_SETUP.md) and [HANDOFF.md](HANDOFF.md). Reading YAML or passing local tests alone does not establish a GitHub pass.

Historical Step05A status: CI PR#2 was open; a pre-run restriction prevented both hosted jobs from starting. Required check settings were proposed but unapplied until an actual baseline succeeded. No PR/main execution pass was verified at that checkpoint; the later owner override keeps Actions disabled.

On 2026-10-12 the owner authorized continuing with local validation while hosted execution remained unverified.
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
Real GDELT429 is a provider availability limit, separate from the unexecuted hosted GitHub checks.

Step09 analytical tables are complete locally on `feat/analytics-marts`, based on Step08 `8dd89ba`.
Shared validation passes139 unit/44 integration tests, six migrations and the packaged hourly SQL
resource. Steps06–09 remain unpushed; no remote check, protection change or CI merge is claimed.
