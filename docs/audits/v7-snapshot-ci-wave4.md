# Independent snapshot CI acceptance (2026-09-08)

Base: fe1bf86. Branch: codex/v7-snapshot-ci-wave4. No product files,
existing harness, migrations, shared runtime workflow or aggregate gate changed.
No AGENTS.md was found in this repository. Base worktree was clean.

## Isolation and protocol

New independent workflow uses its own GitHub job service db (postgres:16-alpine),
python:3.12-bookworm, no published ports/volumes, ephemeral run-scoped test password
and freshly generated application keys. Existing snapshot guard already accepts db.
Wrapper refuses an existing puw_v7_test_snapshot_recovery database before any writes.
Only a database successfully created by this invocation can be dropped. Migration
must reach exactly a54f001c0a20 before the unchanged real worker harness executes.
Its existing empty-business-data guard remains intact. No provider secrets inherited.

Each subprocess starts a POSIX session. Its exact process group is killed/waited
before database cleanup, including timeout or early coordinator exit. If containment
cannot be confirmed, preserve database and report cleanup FAIL. Cleanup failure
also fails overall acceptance. No global process/database cleanup.

Output contains only fixed phases/statuses and strictly validated synthetic proof
fields. Extra fields, booleans masquerading as IDs, invalid timing and invalid proof
status are rejected. No child stderr/stdout is published. Exceptions are not serialized.
Artifact upload is always attempted; absent artifact is an error.

## Deadlines and limitations

Job ceiling 12 minutes; wrapper step 8 minutes. Internal work ceiling 360 seconds,
then 60 seconds reserved for cleanup. Migration and harness each capped at 180 seconds
and remaining work. Admin DB connect/statement/lock bounded 5/5/1 seconds. These are
bounded-failure ceilings, NOT assurance all phases finish. Job reserve assumes setup
finishes in four minutes; hard cancellation, OS/network/filesystem failures can prevent
cleanup or protocol publication. Last SQL operations may extend past a deadline check
by their individual bounded timeout.

Harness deliberately filters child environment: PGCONNECT_TIMEOUT/PGOPTIONS passed
to coordinator are not inherited by its worker children. No claim of per-worker SQL
timeouts; outer process-group deadline contains them on Linux. Runtime can spend real
60-second lease time plus startup/poll/cleanup overhead; timeout is honest FAIL.

This workflow is **independent**, not part of the existing aggregate acceptance gate.
Root must wire cross-workflow/release gating separately. Existing 42 PG proofs unchanged.
API OS restart and live provider acceptance remain NOT_RUN.

## Verification

Local pure unit tests cover accepted/rejected proof, environment isolation, work expiry,
owned process-group timeout, preexisting DB refusal, migration failure skipping harness,
successful cleanup, containment failure preserving DB, cleanup failure and safe output.
Initial test run: 14 passed / 1 failed because Windows lacks os.killpg; test double
was made explicit for POSIX API. No product defect inferred from this portability issue.
Final targeted result: **17 passed, 0 skipped** (0.11s), `git diff --check` PASS.
Command: `python -X utf8 -m pytest scripts/ci/test_v7_snapshot_workflow.py -q`
using D-drive test runtime and explicit D-drive temporary directory.
No Docker/PostgreSQL/GitHub runtime executed.
Actionlint not available on PATH. Runtime status: CONDITIONAL / NOT_RUN.

Files: scripts/ci/v7_snapshot_workflow.py, scripts/ci/test_v7_snapshot_workflow.py,
.github/workflows/v7-snapshot-recovery.yml, this report.
No push, merge, PR or deploy.
