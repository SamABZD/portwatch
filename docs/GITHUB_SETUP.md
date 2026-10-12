# GitHub setup record

Current Step10 state: local orchestration/backfills validated, Steps06–10 unpushed. The existing
CI billing-lock deferral persists: no payment, push, merge or branch-rule/account change.
See [HANDOFF.md](HANDOFF.md) and [ORCHESTRATION.md](ORCHESTRATION.md).

## Verified repository

- Project root: `C:\Users\samab\dataEngineering\portwatch`. The parent `dataEngineering` folder is unrelated and is not a Git repository.
- Authenticated GitHub account and target owner: `SamABZD`, verified with `gh api user --jq .login` and `gh api users/SamABZD --jq .login`.
- Repository: [SamABZD/portwatch](https://github.com/SamABZD/portwatch), created as public during Step 00A. `gh repo view` reports `isPrivate: false` and default branch `main`.
- Only remote: `origin`, with fetch and push URL `https://github.com/SamABZD/portwatch.git`.
- Foundation commit: `01c9f0b0c16e256b6c80f092e41e33097becb8bc` (`Establish PortWatch foundation and GitHub workflow`). Its local HEAD and `origin/main` were observed equal immediately after the initial push. The current branch tip can be checked with `git rev-parse HEAD` and `git ls-remote origin refs/heads/main`; later documentation commits have their own SHAs.
- Repository description: `Local-first Port of Los Angeles AIS data engineering portfolio project`.
- Topics: `ais`, `data-engineering`, `portfolio`, `postgis`, `postgresql`, `python`.

## Operations and checks performed

1. Read the project handoff and confirmed the independent child project root. No existing PortWatch Git repository or remote was present. `gh repo view SamABZD/portwatch` could not resolve a repository before creation.
2. Initialized `main` with `git init -b main`. Reused the existing configured author name and email in repository-local Git settings; no global settings were changed and no identity was invented.
3. Added `.gitattributes`, contribution instructions, pull request template, and GitHub workflow instructions. Staged only explicit PortWatch paths. `git diff --cached --check` passed; the 22-file staged list contained source, lockfile, configuration, and docs only. Git ignore checks excluded `.env`, virtual environments, raw/derived/quarantine data, artifacts, and logs. A staged-content search found no GitHub or OpenAI token-shaped strings.
4. Created one honest foundation commit, then ran `gh repo create SamABZD/portwatch --public --source . --remote origin --push`. The command created the remote and pushed `main`.
5. Set the description and topics with `gh repo edit`. Verified public visibility, default branch, topics, the remote URLs, remote branch SHA, root source tree, and README using `gh repo view`, `git`, and `gh api`.

At the foundation stage, no branch protection, hosted website, or CI run was configured. The public [Flux repository](https://github.com/SamABZD/flux) was used only as a read-only reference. Its published `.gitattributes` has LF-normalized text and CRLF PowerShell scripts; PortWatch adapted that pattern.

## Step 05A implementation and publication status

The Validate workflow and shared verification command are implemented locally. The authenticated account is still `SamABZD`; repository visibility is public, default branch is `main`, and viewer permission is `ADMIN`. Before this stage, remote `main` was `f40700b91de01b0aff3a59c727852277e8755d0f` and contained only the foundation. The user approved publishing the already completed Steps01–05 separately: [prerequisite PR #1](https://github.com/SamABZD/portwatch/pull/1) contains commit `434e5f71373fabcf849f3f659085ce5e363218db` and was merged as `f784a9374942c704630bd412704a67c4896ead24`. Its 41 changed paths excluded private `.env`, the actual AIS credential, and provider/runtime/database artifacts. Pre-CI snapshot versions were staged directly, preserving the current CI changes for a separate PR. That prerequisite PR had no Actions workflow; its recorded checks were local, not remote CI.

Existing protection inspection returned `Branch not protected`; repository ruleset listing returned an empty array. Branch settings are **not applied**, because the required successful baseline has not occurred.

### Actual GitHub attempts: blocked before execution

- Focused [CI PR #2](https://github.com/SamABZD/portwatch/pull/2) is open and attached to the Codex chat, as is prerequisite PR#1. Initial CI code/head SHA: `97b8af116948088bf6730866b77cdffc71f0e16b`.
- [PR run 38171159125](https://github.com/SamABZD/portwatch/actions/runs/38171159125) and [push run 38171158488](https://github.com/SamABZD/portwatch/actions/runs/38171158488) both concluded failure for that exact SHA. GitHub registered job/check names `quality` and `integration`, application ID15368, but neither job received a runner or executed any steps. There are no job logs because no jobs started.
- The annotations on both PR checks report exactly: **“The job was not started because your account is locked due to a billing issue.”** Repository Actions are enabled with all actions allowed. This is an account-level execution blocker; no remote lint, test, package, PostGIS service, or migration pass is claimed.
- CI PR remains unmerged. Remote main is the prerequisite merge `f784a9374942c704630bd412704a67c4896ead24`; its Actions list is empty because the workflow is not on main yet. A resulting-main CI run cannot be verified until the setup PR passes and merges.
- Local shared validation passed independently: 87 unit/12 integration tests, Ruff lint/format, real wheel import/resources/CLI, fresh/repeated migrations. The second run used the documented `uv run --locked --extra dev python scripts/validate.py` command and passed in 26.57s unit/6.29s integration. Eleven formatting-only files had identical Python ASTs; SQL migration bytes match the saved prerequisite snapshot.

### Required user action and resume

Inspect GitHub Settings **Billing & Licensing** for `SamABZD`, then resolve the account lock. To keep the $0 constraint, review GitHub's [locked-account guidance](https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account), including its GitHub Free option where applicable, or [contact GitHub Support](https://docs.github.com/en/support/contacting-github-support) about blocked free public Actions. No purchase, payment, plan change, or support message was made by this implementation. Account plan/payment decisions require the account owner.

After GitHub clears the lock, resume Step05A: identify PR#2's latest head/run, rerun it, fix any actual execution failures, and confirm both jobs pass on that intended SHA. Apply the rule below only after a successful baseline, merge PR#2 while matching its head SHA, then verify the resulting main push run. Record successful URLs and SHAs before marking Step05A complete.

On 2026-10-12, reran PR run38171408548 as attempt2 on latest CI head `35b230c6d40092d45fb9e7876f53b4fc9f9f649c`. It failed at `2026-10-12T10:26:37Z`; both new job checks had no steps and the same account billing-lock annotation. The owner declined paying to clear the lock and authorized choosing how to proceed. Continue local stages at $0 with CI explicitly pending; the prior pre-Step06 remote gate is deferred by that authorization. Step06 was completed on local branch `feat/geospatial-zones`, based on this pending CI head; its source has not been pushed. This does not authorize weakening existing protection, merging unchecked CI, or making account/payment changes. Main and remote PR remain unchanged.

### Exact proposed main protection (unapplied)

Step07 has subsequently completed locally on `feat/visits-waiting`, based on Step06 checkpoint
`a068ef69d055eb1cf3dd7bbd88fa3bcbab882c05`. Both stages remain unpushed. Local validation now
passes 109 unit/22 integration tests with four migrations. The CI deferral and $0 authorization
remain in force; local checks do not establish hosted GitHub execution.

Step08 now adds local weather/news connectors on `feat/weather-news`, based on Step07
`ac684739f794997d67a63a9a97d403a99c1c1477`. Local checks pass134 unit/32 integration tests
with five migrations. No pushes, unchecked CI merge, protection changes or payments were made.

Step09 analytical tables now run locally on `feat/analytics-marts`, based on Step08 checkpoint
`8dd89ba51922cf972504a7bc188e67e558072886`. Shared validation passes139 unit/44 integration
tests and six migrations. Original remote CI/account state has not been changed by this stage.

Apply using `PUT /repos/SamABZD/portwatch/branches/main/protection`, after rechecking for any stricter rules and observing actual successful check names:

```json
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "quality", "app_id": 15368},
      {"context": "integration", "app_id": 15368}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```
