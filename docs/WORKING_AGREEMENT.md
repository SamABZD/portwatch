# Working agreement

For every stage:

1. Read the current stage prompt, applicable `AGENTS.md`, the working tree, relevant code, `DECISIONS.md`, and `HANDOFF.md` before editing. Preserve existing work and implement only the assigned stage.
2. Run checks relevant to the files changed and record the exact commands and observed outcomes. Never claim a command, query, metric, provider coverage, or result succeeded without seeing it succeed.
3. Save original provider output unchanged before transformation. Do not overwrite raw data or merge historical and live AIS without a documented precedence policy. Keep synthetic test data isolated from analytical data.
4. Keep credentials private, use environment variables, and do not print real keys or commit `.env`. Use $0 local resources unless the stage and user explicitly authorize otherwise.
5. Store analytical timestamps in UTC and note source timestamp quality. Treat source failure, empty result, stale data, and a valid zero separately.
6. Update `DECISIONS.md` for architectural or analytical decisions and `HANDOFF.md` for completed work, verified commands, blockers, data locations, and the next step.
7. End with changed files, exact commands run, observed results, unresolved prerequisites, and any action the user must perform. Stop at the assigned stage boundary.
