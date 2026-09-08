# v7 wave5: draft graph lifecycle and saved critical plan

Date: 2026-09-08. Branch: `codex/v7-execution-wave5`.
Base: `63e3b660049915ae341fb3b68587f3c0eaac09ed`.
Decision: **CONDITIONAL**. No whole-MVP or production acceptance claim.

## History

- `2a40117` ← `0ce33739722f0ead2dba0b6ea1823b1f79839c34`: atomic graph rows.
- `26d781b` ← `4aa0f37f3b1028767b4e62e56fefce072d8ab7cf`: server plan display.
- `75d6440` ← `e613c92d7e70f2ebc3d0816a734347b6b7bc29ab`: graph row controls.
- `38404fd` ← `753ff437354646980f28145876f702d8d0631f8f`: wave4 full-test report.
- Root integration: actual-App row/browser coverage and two new mandatory PG
  test pins. No cherry-pick conflicts. Shared App was not changed by these streams.

## Behavior

`PUT /execution/baselines/{id}/graph/rows` accepts the complete resulting draft
graph, explicit deleted IDs, exact revision and scoped client references for new
rows. It validates the entire plan before publication; partial batches do not
commit. Financial/source/actual-bearing rows cannot be silently unlinked by delete.
Approved/superseded plans remain immutable. The existing planner is reused.

The existing editor now exposes add/rename/delete with separate explicit save.
Dependency references to new rows are mapped to stable server IDs. The legacy
parameter editor and lifecycle editor cannot concurrently save dirty snapshots.
409 retains local changes; no silent replay. Approval remains a separate action.

Critical IDs/edges, total/free float and saved revision are read from the exact
server plan. Unknown/incomplete/mismatched data is denied. Client code does not
recalculate dates or invent zero slack. Working calendars remain explicitly
unsupported; v7 allows them after baseline acceptance. WBS is not implemented by
flat row lifecycle.

## Verification

- Integrated graph/backend target: **325 passed, 2 PostgreSQL skipped**, 6.51 seconds.
  Includes planner, import validation, migration, boundary/API and row lifecycle.
  First command named two nonexistent test files, collected none; corrected
  invocation used the actual file inventory. No tests were removed.
- Full frontend: **350 passed**, 36 files, 27.30 seconds. TypeScript PASS.
- Actual-App Chromium synthetic graph E2E: **5 passed**, 14.7 seconds, including
  dependent-row insertion and protected-delete 409, existing save/approve/rebase
  and delayed new-project membership.
- Browser harness built the candidate successfully; >500 kB chunk warning remains.
- Targeted CI contracts: **75 passed**, 1.16 seconds. Original 37 + 5 meeting +
  2 graph-row PostgreSQL pins = 44; head/deadlines unchanged.
- Base wave4 full backend: **2352 passed, 55 skipped**, 1233.54 seconds. This is
  base regression evidence, not an uninterrupted full suite for wave5's added rows.
- Alembic unchanged `a54f001c0a20`; no model/migration/planner changes.

## Acceptance boundaries

PostgreSQL tests are prepared, not executed. The concurrent FK test exercises a
concurrent link/delete safety scenario; its event occurs before lock acquisition,
so it does not independently prove observation of an actual blocking wait.
Real adapters, complete business restore, original immutability across live import,
WBS and unified DDS acceptance remain separate. Snapshot workflow must be checked
separately from the older aggregate gate. No real emails/files/credentials used.

No push, merge, PR, deployment or dirty-worktree changes. Tests/cache/build remain
on D. This candidate does not include the separate wave6 DDS stream yet.
