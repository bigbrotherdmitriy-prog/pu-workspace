# MVP-2: narrow hardening of the PR #33 recovery contract

Date: 2026-09-19. Branch: `codex/mvp2-unknown-recovery`.
Base HEAD: `e9769b29cbec8cacbd996032b479364c0f2c78cb` (merge of PR #33;
PR commit `813d613025dd76fec226151da6ea39e946e32565`).
Status: **IMPLEMENTED; OFFLINE AND ISOLATED POSTGRESQL VALIDATION PASS**.
Prepared for an owner-authorized pull request after complete local regression.
No merge, deployment or Task #653 mutation is authorized by this validation.

## Accepted contract, unchanged

Keep `/reconcile`, explicit manager `/resolve-not-applied`, the historical action's
`UNKNOWN -> NOT_APPLIED` transition, and a separate confirmation for publication.
No automatic UNKNOWN reset. No new migration, models, UI, `/recovery-checks`, or
`/reissue`. The single Alembic head remains `e73c2b4a901d`.

The previous independent implementation was preserved in
`D:/PU-Workspace/backups/mvp2-unknown-recovery-before-pr33-20260919/`, including
the conflicted application, and stash `55ab1bd04096c639a9f6640fcd6c4effce2cd0d8`.
Its changes were withdrawn before this selective implementation. None of its
previous test results is evidence for this implementation.

## Changes

1. Expand existing PostgreSQL outbox tests, retaining the four earlier cases.
   Added concurrency, rotation/revocation, expired lease, crash after read or
   observation, late positive versus concurrent dispatch, and final-CAS row-lock
   tests. Separate sessions and Barrier/Event gates create real DB concurrency.
   Provider calls in these tests are synthetic: this is NOT live Google evidence.
2. An original, validated late `APPLIED` after explicit human absence resolution
   is retained as a new append-only observation. Explicit recovery republication
   links use existing `AuditLog`, checked against sealed action envelopes and
   the original human-resolution observation. Only linked, unattempted recovery
   descendants become `BLOCKED`; ordinary edits are not inferred as recovery from
   revision order. Queue/dispatch cannot bypass this fence. Already attempted
   descendants retain their facts and produce a conflict audit; no automatic
   deletion or rollback is performed. No retroactive exactly-once guarantee is
   claimed for an effect that already crossed the provider boundary.
3. Rotated-credential recovery requires existing verified
   `ConnectionIdentity`/`MailboxCredentialGeneration` history predating the action
   and continuity through subsequent reconnects. Token and identity generations
   are independent counters. Missing/ambiguous history or a different/revoked
   identity/binding epoch denies recovery, without invented backfill.
4. Tasks search distinguishes complete absence from multiple distinct matches,
   malformed/incomplete pages, repeated page tokens and a 100-page limit. These
   uncertain outcomes remain UNKNOWN and cannot authorize absence resolution.
   Different error/absence safe codes are not deduplicated as the same evidence.
5. Task/Calendar lookup credentials are copied from the exact selected token
   generation into memory; refresh cannot persist credentials in the DB. A final
   same-transaction check fences authority, account pins and durable worker claim
   before ledger/registry projection. Locks include credential, project, approval,
   membership, Task and verified identity rows as applicable. OAuth callback
   serializes updates on its token row. Negative observations are bound to their
   exact credential/authority pins in existing audit records; another reconnect
   requires a fresh check before manual NOT_APPLIED.

## Verification

| Check | Result |
|---|---|
| Targeted synthetic outbox/runtime/guard tests + workflow contract | 97 passed, 0 failed/errors/skipped; 17.92 s |
| scripts/ci pre-PR regression | 139 passed, 0 failed/errors/skipped; 99.89 s |
| Full backend pre-PR regression | 1700 passed, 0 failed/errors, 51 skipped; 310.08 s |
| Frontend pre-PR regression | 203 passed, 0 failed/skipped, 36 files; Vitest 26.75 s |
| TypeScript `tsc --noEmit` | PASS; 9.34 s |
| Production `tsc -b && vite build --configLoader runner` | PASS; 14.64 s (Vite 4.97 s) |
| Real PostgreSQL recovery suite | 18 passed, 0 failed/errors/skipped; 9.28 s, PostgreSQL 16.15 (4 retained + 14 added) |
| Alembic heads | One unchanged head: `e73c2b4a901d` |
| git diff --check | PASS |
| Existing tracked secret gate + added-file high-confidence secret patterns | 0 findings; not a proof against every possible secret format |
| UI/models/migrations/public route changes | None |

The first backend run completed with 1687 passed, 0 failed/errors, 50 skipped in
354.77 s. It started before the final race hardening/tests were frozen and is NOT
the final regression verdict. The final rerun completed with 1700 passed,
0 failed/errors, 51 skipped in 283.62 s. All 18 outbox PostgreSQL cases were
skipped in that offline run; they are NOT counted as PostgreSQL runtime evidence.
The final subprocess also used Git Bash first in PATH.

An additional owner-requested full pre-PR run on the same frozen backend source
finished with **1700 passed, 0 failed/errors, 51 skipped in 310.08 s**. Live-related
environment variables were cleared in that subprocess; SQLite remained the
offline DB. Real PostgreSQL evidence is the separate 18/18 run below. All 139
CI contract tests passed again in 99.89 s after adding an explicit CI-only PyYAML
installation, required by the new workflow contract test (no application
dependency change). An assertion prevents that CI prerequisite being omitted.

Frontend used existing dependencies with exactly matching package/lock hashes:
Vitest 4.1.11, Node 24.20.0. No manifest/lock changes. All 203 tests, TypeScript and
production build passed. The only build warning is the existing chunk-size
recommendation: 587.47 kB JS versus 500 kB. Generated build files were archived on
D and only this run's generated changes were restored, keeping this PR backend
scoped. Independent read-only review found no additional blocking defects.

The main branch was fetched before publication: `a59094cb0423c5434ba54f2b7ca1dbf30c3cf905`
includes unrelated invoice classification PR #34. This branch remains based on
PR #33; none of its changed paths overlaps the incoming invoice changes. GitHub
pull-request CI must validate the combined merge ref before claiming green CI.

Frozen runtime file SHA-256 values for matching future PG execution to this run:

- `product.py`: `56F5F73D28E5BEE52DCC2716A8ACBBA5FE20AA945DD021B5D6ED2B24EE7EFF67`
- `runtime.py`: `4258ADDE320096013B55BE62FA2B746C532322B6E9B4A27A2DFABF3CEF0CD24A`
- `account_guard.py`: `7F9F2C95C15EC15D36044B23F910787EF7E40854A18EF803C999B5350D2B4DA4`
- `recovery_guards.py`: `B81E2A7219790C7CF1BFAB56D5CF1C1BB93053EFFE15DE8BA55B6426C5BCBE6A`

Artifacts on D:

- `D:/PU-Workspace/temp/pr33-hardening-targeted-final.xml`
- `D:/PU-Workspace/temp/pr33-hardening-ci-final.xml`
- `D:/PU-Workspace/temp/pr33-hardening-full-backend-final.xml` (final rerun)
- `D:/PU-Workspace/temp/pr33-pre-pr-backend.xml` and `.log` (latest full backend)
- `D:/PU-Workspace/temp/pr33-pre-pr-ci.xml` and `.log` (latest CI contracts)
- `D:/PU-Workspace/temp/pr33-hardening-final-frontend.xml` and `.log`
- `D:/PU-Workspace/temp/pr33-hardening-final-typescript.log`
- `D:/PU-Workspace/temp/pr33-hardening-final-build.log`
- `D:/PU-Workspace/temp/pr33-hardening-final-production-build.tar`

The initial local CI-suite run failed because an existing smoke-workflow test
selected the Windows WSL bash shim without a usable Linux shell. Rerunning with
Git Bash first in the subprocess PATH passed all 139 tests; that unrelated
smoke-workflow test was not changed.

## PostgreSQL runtime validation

The existing CI workflow now creates and migrates a separate `puw_track_e_test`
database, explicitly sets `PUW_TRACK_E_TEST_DSN`, runs the entire existing outbox
PostgreSQL file, and rejects empty JUnit results or any skipped test. Logs and
JUnit are uploaded by the existing verification artifact step. This workflow has
not been pushed or run on GitHub for these uncommitted changes.

Local PostgreSQL/Docker was not available. After earlier tool approval rejection,
the owner explicitly authorized a 15-file, code-only transfer to `72.56.108.162`
for an independent temporary validation environment. The final payload contained
no `.env`, keys, credentials, database copies or `.git` directory. Known-secret
pattern scan: 0 findings. Payload SHA-256:
`50a69e59972405fc6bc85d60a87a2dd71942365429344bd41545a3c1b72d4763`.

Execution on 2026-09-19, 09:51:07–09:51:25 UTC:

- Temporary directory: `/opt/puw-pr33-hardening-validation-20260919`.
- A read-only Git archive of server-local base `689928e5c3fd38093ca57b48230b723f71514cf0`
  was extracted there, then overlaid with exactly the 15 authorized files. This
  includes all seven PR #33 changed paths between that base and local HEAD, plus
  the current hardening source/tests. Every transferred file hash matched locally.
- Existing backend image was used only as a dependency runtime in a NEW runner;
  source mounted read-only. No application/worker entrypoint was started.
- NEW PostgreSQL 16.15 container: `--network none`, no published ports, tmpfs data,
  no persistent volume. Runner shared only this container's network namespace.
  No production/live-test env, mounts, database or provider credentials were used.
- Both `DATABASE_URL` and `PUW_TRACK_E_TEST_DSN` explicitly selected the isolated
  `127.0.0.1` database `puw_track_e_test`. Fresh Alembic upgrade succeeded;
  `heads` and `current` both returned the single `e73c2b4a901d` head.
- Python 3.12.14, pytest 9.1.1, complete file run without test filters or xdist:
  **18 passed, 0 failed, 0 errors, 0 skipped in 9.28 s**. JUnit time: 9.276 s.
  Runner exit code 0, OOM false; container lifetime including migrations: 18.23 s.
- All six result artifacts were copied to D and their SHA-256 values verified
  against server originals before cleanup. JUnit independently checked for exactly
  18 nonempty cases, no failures/errors/skips. This is real database/concurrency
  evidence with synthetic provider effects, NOT live Google acceptance.

Exact test results, all from `backend/tests/test_mvp2_provider_outbox_postgres.py`.
Times below are JUnit per-case totals (including setup/teardown).

| Test | Result | Seconds |
|---|---|---:|
| `test_two_simultaneous_confirms_create_one_action_and_one_job` | PASSED | 0.303 |
| `test_approved_batch_retains_live_authority_after_queue_projection_on_postgresql` | PASSED | 0.233 |
| `test_revoked_approval_cannot_dispatch_on_postgresql` | PASSED | 0.131 |
| `test_crash_after_calendar_effect_uses_lookup_not_second_insert_on_postgresql` | PASSED | 0.302 |
| `test_concurrent_rotated_reconciliation_requests_create_one_job_on_postgresql` | PASSED | 0.328 |
| `test_concurrent_absence_resolution_and_reconfirmation_are_single_winner_on_postgresql` | PASSED | 0.591 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[False-rotation]` | PASSED | 0.455 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[False-identity_revoked]` | PASSED | 0.439 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[False-lease_expired]` | PASSED | 0.382 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[True-rotation]` | PASSED | 0.473 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[True-identity_revoked]` | PASSED | 0.377 |
| `test_lookup_result_is_fenced_when_account_or_claim_changes_on_postgresql[True-lease_expired]` | PASSED | 0.353 |
| `test_reconciliation_crash_retries_only_lookup_on_postgresql[after_read]` | PASSED | 0.599 |
| `test_reconciliation_crash_retries_only_lookup_on_postgresql[after_observation]` | PASSED | 0.577 |
| `test_late_applied_receipt_wins_over_inflight_empty_lookup_on_postgresql` | PASSED | 0.489 |
| `test_late_original_applied_fences_concurrent_republication_dispatch_on_postgresql` | PASSED | 0.544 |
| `test_final_reconcile_validation_holds_authority_rows_until_commit_on_postgresql[approval]` | PASSED | 0.686 |
| `test_final_reconcile_validation_holds_authority_rows_until_commit_on_postgresql[task]` | PASSED | 0.731 |

Evidence retained on D:
`D:/PU-Workspace/temp/pr33-hardening-postgres-20260919/` contains JUnit, full runner
log, exit/timing metadata, PostgreSQL/schema version, payload hashes, original
container identity/start-time snapshot and `SHA256SUMS`.

Cleanup completed at **2026-09-19T09:52:51Z**. Exactly the new validation runner,
new PostgreSQL container (ephemeral synthetic database), and the 21 MB temporary
directory including transferred files/archive were removed. Absence verified.
All 22 pre-existing containers retained the same IDs/start timestamps before
cleanup. Production and `puw-mvp2-live-test` were not modified or restarted.
The old, unrelated `/opt/puw-recovery-validation-20260919` was not touched.

One preparation command was rejected before execution because it mistakenly
targeted an archive output inside the production release directory. It was not
executed; the corrected command streamed a read-only Git archive into the
authorized temporary directory instead. No production files were written.

## Files

- `.github/workflows/ci.yml`
- `backend/app/api/google_drive.py` (token-row lock only)
- `backend/app/provider_actions/contracts.py`
- `backend/app/provider_actions/product.py`
- `backend/app/provider_actions/runtime.py`
- `backend/app/provider_actions/account_guard.py` (new)
- `backend/app/provider_actions/recovery_guards.py` (new)
- `backend/tests/test_mvp2_provider_outbox.py`
- `backend/tests/test_mvp2_provider_outbox_postgres.py`
- `backend/tests/test_provider_recovery_guards.py` (new)
- `scripts/ci/test_provider_outbox_workflow.py` (new)
- `docs/audits/mvp2-pr33-recovery-hardening.md` (this report)

## Scope limitations

- Historical actions without sufficient pre-action verified identity history
  require an explicit investigation; current credentials cannot manufacture it.
- Previously created unlinked PR #33 republications are not guessed from revision
  numbers. The new explicit link is written when the normal confirmation path
  creates a recovery republication.
- Provider readback here is tested synthetically. No real Google objects were
  created, changed, read or deleted during this implementation.
- Production and local Task #653 remain untouched.
