# GitHub workflow

## Repository boundary

This repository contains only PortWatch. Its root is the `portwatch` directory, not the parent `dataEngineering` folder. `main` is the default branch. `origin` must identify only `SamABZD/portwatch`; verify it with `git remote -v` before pushing. The public [Flux repository](https://github.com/SamABZD/flux) is a read-only organization reference, not a remote or source tree for PortWatch. Its text-normalization pattern informed `.gitattributes`; its CI design will be adapted at Step 05A, when the necessary tests exist.

## Normal change routine

1. Read the assigned prompt, project brief, working agreement, decisions, handoff, and affected code. Inspect `git status --short --branch` and `git remote -v`.
2. Update `main` normally, then make a scoped branch such as `feat/historical-ingestion` or `feat/map-replay`. Do one stage at a time.
3. Implement the stage, run relevant local checks from `CONTRIBUTING.md`, and record exact commands and observed results. A missing Docker service is a blocker, not a test pass.
4. Update `docs/DECISIONS.md` for new choices and `docs/HANDOFF.md` for stage state, blockers, data locations, and the next step.
5. Inspect `git status --short`, stage explicit paths, inspect `git diff --cached --check` and `git diff --cached --stat`, and confirm the staged list contains no private or bulk data. Make a meaningful commit. Do not manufacture development history.
6. Push the branch and open a pull request to `main` with the stage scope, validation evidence, data/schema impact, and blockers. Review the diff before merging. Keep commits scoped and avoid force-pushing published history merely to tidy it.

Use `git check-ignore -v <path>` to confirm an artifact is excluded. To unstage an accidental file, use `git restore --staged -- <path>`. If already committed, remove it from tracking in a new correction commit. For a published secret, revoke or rotate it first, then coordinate repository cleanup; history rewriting alone does not revoke a credential.

## Verification gates

Run `ruff check .` and the focused tests through `uv run` locally. Run `python -m portwatch doctor` when the local database is available. Step 05A will add a GitHub Validate workflow with an isolated PostGIS service and a verified pull request run. No CI result is implied before that stage.
