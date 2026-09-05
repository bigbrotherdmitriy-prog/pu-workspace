# Runtime claim and workflow repair — 2026-09-05

## Verified publication and CI evidence

The explicitly authorized commit `2848bba170025fbc84d3d3d7bff35d1e19e13739`
was pushed without force to `codex/mvp1234-wave2-integration`. No merge, PR or
production deployment was performed. Runs below were matched by exact head SHA:

| Run | Actual result |
| --- | --- |
| [PU Workspace CI 33977163601](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33977163601) | PASS |
| [Docker smoke 33977163654](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33977163654) | PASS |
| [v54 runtime 33977163590](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33977163590) | FAIL: storage PostgreSQL recovery phase; local-engines and lint jobs PASS |
| [Storage workflow 33977162995](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33977162995) | FAIL before jobs; invalid workflow context |

The runtime safe `protocol.json` was read from artifact `9972661605` in memory.
No signed download URL, raw stderr, DSN, secret or source document is stored here.
Protocol: `puw.v54.runtime.protocol.v1`; head `a54f001c0a18`; migration PASS
(2.47 s), storage migration PASS (2.44 s); `postgres_mvp1_storage` FAIL
(5.81 s), one test passed, failed location
`backend/tests/test_mvp1_storage_mutation_postgres_runtime.py:110`.
Cleanup PASS; `raw_output_published=false`. Subsequent mandatory PostgreSQL
phases and process-fault scenarios were NOT_RUN, not individually failed proofs.
The aggregate corpus field was not reached; this is not a diagnosed corpus defect.

## Regression-first repairs

1. PostgreSQL `claim` executes raw UPDATE SQL. In an `expire_on_commit=False`
   session, `db.get` returned the already-loaded old worker row. The update was
   committed but the returned ownership fence was stale. Refresh only the return
   read using `populate_existing=True`. Do not alter claim predicates, attempts,
   lease duration, SQL locking or the queue model.
2. Storage workflow used `job.services` where the GitHub job-level environment
   does not expose that context. Move dynamic-port resolution to a step, validate
   the port, mask the disposable test DSN and export it through `GITHUB_ENV`.
   No production environment or credential is used.
3. Independent review identified the next unreachable runtime test line:
   `execution_owner(*owner)` passed four positional arguments to a function whose
   fence arguments are keyword-only. Preserve all four values and pass `attempt`
   and `locked_at` explicitly. The offline regression executes the actual AST
   call from the PostgreSQL harness and checks the full context fence and reset.

All three regressions failed before their fixes. The identity-map test uses a
real SQLAlchemy Session and identity map, substituting only PostgreSQL transport
with a SQLite UPDATE; it is NOT a concurrency substitute. An independent reviewer
also ran the existing complete crash/replay Python path against SQLite, preserving
the one-provider-effect and one-receipt assertions. Real PostgreSQL rerun is required.

## Local verification

Working directory: the separate integration worktree. Python is the existing
workspace `.venv-pu-workspace-tests/Scripts/python.exe`; no dependency changes.

```powershell
# From backend/
python -X utf8 -m pytest tests/test_queue_claim_identity_map.py tests/test_durable_jobs.py tests/test_job_hardening_contract.py tests/test_mvp1_storage_mutation_runtime.py tests/test_mvp1_storage_mutation_repository.py ../scripts/ci/test_storage_workflow_context.py -q --tb=short
python -X utf8 -m pytest -q --tb=short --basetemp=.pytest-ci-repair-full
# From repository root, Git Bash available on PATH and PYTHONUTF8=1
python -X utf8 -m pytest scripts/ci -q --tb=short --basetemp=.pytest-ci-repair-scripts
actionlint .github/workflows/storage-mutation-runtime.yml .github/workflows/v54-pilot-runtime.yml
git diff --check
```

- Targeted: **25 passed**, 1.30 s.
- Full backend: **1730 passed, 36 skipped**, 504.03 s; 33 existing Alembic
  `path_separator` deprecation warnings. Local skips remain PostgreSQL/local
  engine/Windows symlink environment cases, not disabled assertions.
- CI scripts: **218 passed**, 100.37 s.
- Actionlint 1.7.12: PASS; diff check: PASS (line-ending notices only).
- The sole schema head remains `a54f001c0a18`; this repair adds no migration.

Frontend meeting-source work and other agents' implementation are separate from
this minimal CI repair commit. Its results must not be attributed to a later
integrated product SHA without rerunning tests.

## Decision

**CONDITIONAL** until a new exact-SHA isolated PostgreSQL run proves recovery and
all later mandatory phases. The old run stays FAIL; a local green suite cannot
change that result. Original dirty worktree, production, real provider accounts
and documents are unchanged. Full TZ completion is not claimed.
