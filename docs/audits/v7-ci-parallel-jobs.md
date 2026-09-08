# V7 independent backend/frontend CI jobs

Date: 2026-09-08. Branch: `codex/v7-ci-parallel-jobs`.
Base: `fbbf12c7c5913874af1a02332724a1d23927e0a5`.

## Audit and change

Previously the single `test-and-build` job ran PostgreSQL migrations and complete backend pytest before installing/checking/testing/building frontend. These runner workloads have no data dependency. The workflow now has independent `backend` and `frontend` jobs with no `needs` edge between them. PostgreSQL service and database environment remain exclusively with backend. Runner and language/package versions, locked install commands, pip/pnpm caches, migration invocation, full pytest and frontend check/test/build commands are preserved. No xdist, per-test sharding, skip or reduced selection introduced.

The old `test-and-build` identifier is retained as the aggregate job to avoid intentionally renaming its required-check identity. It has `needs: [backend, frontend]` and `if: always()`. Its embedded Python reads GitHub `toJSON(needs)` via environment, requires exactly those two jobs with exact `success`, and fails on missing, malformed, skipped, cancelled, failed or unknown state. Output is only aggregate PASS/FAIL, not raw needs/env. A cancelled entire workflow cannot prove successful acceptance. Existing branch-protection configuration was not queried or modified.

Each producer uploads only its own log paths with `always()` and a distinct name containing run ID/attempt. Original retention/missing-file policy is preserved. Upload failures remain job failures and therefore block the aggregate. The aggregate consumes job outcomes, not another runner's local files. No deploy job or new credentials.

## Regression-first verification

New structural tests initially produced **11 failures** against the old serial workflow. This is missing requested orchestration, not an application defect.

Test command (D-only temporary files):

```text
D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest scripts/ci/test_v7_parallel_ci.py -q --tb=short --basetemp=D:/PU-Workspace/tmp/parallel-ci-final
```

Final: **15 passed**. YAML is parsed from the actual workflow. Tests extract and execute the actual embedded aggregate Python; they do not reimplement its result algorithm. Success, missing/skipped/failure/cancelled jobs, wrong result type, non-object JSON, missing results and extra unknown jobs are checked. Command retention, pipefail, service isolation, locked dependencies, caches, credentials persistence and runner-local artifact paths are asserted.

`git diff --check`: PASS. Actionlint is not available on PATH in this environment and was not installed. GitHub runner execution and elapsed-time improvement were not measured; no runtime PASS or quantified speedup is claimed. Parent integration owns the complete CI contract suite. No heavy application tests/builds run in this bounded package.

## Boundaries

Exactly `.github/workflows/ci.yml`, `scripts/ci/test_v7_parallel_ci.py` and this report changed. No other workflows, Core, root dirty worktree, production, health commands or database isolation mechanisms changed. No push, merge, deployment or installation performed.
