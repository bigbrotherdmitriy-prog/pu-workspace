# v7 authority CI diagnosis — fixture clock and safe thread attribution

Date: 2026-09-08. Branch: `codex/v7-authority-ci-diagnosis`.
Base: `fbbf12c7c5913874af1a02332724a1d23927e0a5`.
Isolated D worktree, initially clean. No AGENTS.md found in the source tree or
previously checked D parent directories. No application code, workflows, runner
scripts, secrets, production, push, merge or deployment changes.

## Evidence and cause boundary

Root supplied run `34205918687` safe failure location:
`test_v54_authority_postgres.py::test_postgres_role_change_linearizes_before_dispatch_check:85`.
The original protocol has no outcomes list or thread exception. That location
alone does **not** distinguish an authorization bypass from a worker failure.

Read-only inspection found a deterministic fixture inconsistency:

1. `seed_authority` stores `AuthorityState.updated_at = NOW`.
2. The old PostgreSQL concurrency test uses `AuthorityResolver(clock=lambda: NOW)`.
3. `change` assigns that same timestamp while increasing authority epoch/version.
4. Existing `_authority_epoch_guard` requires changed `updated_at` history for a
   protected mutation. PostgreSQL preserves timezone-aware datetimes, so assignment
   of exactly the seeded timestamp has unchanged attribute history and raises
   `ValueError("authority_epoch_required")` before `changed.set()`.
5. SQLite round-trips datetimes without timezone information; its naive/aware
   difference masks this fixture problem in ordinary local resolver tests.

This behavior was reproduced against the **real ORM guard**, using SQLAlchemy
committed attribute history with aware timestamps, not an emulated SQL engine.
The first regression run failed with the exact guard ValueError. It is ORM-history
evidence, **not** a real PostgreSQL runtime run. The next isolated CI run is still
required to connect it conclusively to the reported run and reveal other failures.

## Minimal correction

The PostgreSQL test now uses `AUTHORITY_MUTATION_NOW = NOW + 1 microsecond` for
both revocation clock and dispatch check. Seed remains unchanged. No epoch,
authorization, model event or locking guard is weakened. The exact expected
`["denied", "revoked"]` result remains mandatory.

The regression explicitly proves same aware timestamp still fails the unchanged
guard, while the test's distinct timestamp passes it. A possible production
same-timestamp/coarse-clock edge remains outside this patch: changing the model
invariant or resolver clock behavior needs a separate threat/transaction review.

## Thread failure and cleanup diagnostics

Each contender captures only an allowlisted exception class code, not exception
messages, raw traceback, SQL, parameters, DSN, roles or document contents. A thread
error becomes an explicit failing assertion, never a swallowed failure. Expected
AuthorityDenied during dispatch remains the positive denial outcome.

Original Event waits of 5 seconds, pass/fail joins of 10 seconds, lock timeout of
8 seconds and statement timeout of 15 seconds are retained. No test deadline is
relaxed. Finally releases test Events and joins started threads again for teardown
only; an earlier timeout is already a failing result and cannot become PASS.
Schema DROP occurs only after no test contender remains alive and has explicit
lock/statement limits. If a contender still cannot terminate, cleanup fails and
refuses to drop an active schema instead of racing it. Runtime cleanup is NOT
verified locally; no zero-resources claim is made.

### Exact runner integration contract

Only on failure, pytest captured stdout contains one compact JSON line:

```json
{"error_code":"ValueError","phase":"revoke_change","probe":"authority_concurrency","status":"FAIL"}
```

Parser must require **exactly** these four keys:

- `probe`: exactly `authority_concurrency`.
- `status`: exactly `FAIL`.
- `phase`: one of `revoke_change`, `revoke_staged`, `revoke_commit`,
  `revoke_committed`, `dispatch_waiting`, `dispatch_require`, `dispatch_denied`,
  `dispatch_allowed`, `outcomes`, `threads`.
- `error_code`: one of `AssertionError`, `AuthorityDenied`, `OperationalError`,
  `IntegrityError`, `DBAPIError`, `TimeoutError`, `ValueError`, `UnexpectedError`,
  `ThreadTimeout`, `UnexpectedOutcome`.

All other keys, values and shapes must be rejected. Unknown exception types map
to the fixed `UnexpectedError` code. Successful concurrency emits no sentinel.
The existing assertion details also contain a sanitized snapshot for developer
use, but **the runner must not export arbitrary assertion text**. No `-s`, raw
traceback or arbitrary stderr artifact is needed. Root owns the separate strict
parser integration; no shared CI script is edited here.

## Regression results

Run from backend, with TEMP/TMP on D and Python
`D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8`:

```text
python -X utf8 -m pytest tests/test_v7_authority_ci_diagnostics.py -q --basetemp=D:/PU-Workspace/tmp/authority-diagnostic-red --tb=short
```

Diagnostics RED: **3 failed**, missing safe thread helpers.

```text
python -X utf8 -m pytest tests/test_v7_authority_ci_diagnostics.py::test_aware_seed_timestamp_requires_distinct_mutation_clock -q --basetemp=D:/PU-Workspace/tmp/authority-clock-red --tb=short
```

Clock RED: **1 failed**, actual `_authority_epoch_guard` raises
`authority_epoch_required` with equal aware timestamps.

```text
python -X utf8 -m pytest tests/test_v7_authority_ci_diagnostics.py tests/test_v54_authority.py tests/test_v54_authority_postgres.py -q --basetemp=D:/PU-Workspace/tmp/authority-clock-diag-final --tb=short
```

Final: **14 passed, 2 skipped in 15.50s**. Skips are real PostgreSQL authority
concurrency and migration tests: dedicated authority URLs were not configured.
New checks cover equal aware timestamp behavior, safe handling of a DBAPI error
containing synthetic secret/SQL parameters, unknown type/phase suppression, exact
fixed-enum sentinel shape and silence on success. `git diff --check`: PASS.

## Result

**CONDITIONAL.** A concrete fixture defect is reproduced and corrected; offline
diagnostic and authority regressions pass. Real PostgreSQL concurrency, cleanup,
migration and the repaired GitHub run are NOT RUN by this stream. No production
authority defect or complete runtime PASS is claimed. Root must integrate the
strict probe parser and rerun isolated CI on the combined SHA.

Changed files: the PostgreSQL test, new offline diagnostic regression module,
and this report. Worktree is to be clean after the isolated commit.
