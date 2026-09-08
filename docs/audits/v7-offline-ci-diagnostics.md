# Offline CI failure diagnostics — 2026-09-08

Base: fbbf12c7c5913874af1a02332724a1d23927e0a5.
Branch: codex/v7-offline-ci-diagnostics; independent initially clean D worktree.
No AGENTS.md was found in the repository during the preceding base audit.

## Observed problem

GitHub runtime workflow 34205918687, offline artifact 10047977714, reports FAIL,
2416 passed, 55 skipped, 380.59 seconds. Existing evaluator intentionally publishes
neither raw output nor failure counters/IDs. Available job log contains no failed
test identity. Exact underlying test failure is therefore **not established**.
This commit improves diagnostics, not an alleged product defect or a runtime PASS.

## Minimal change

`scripts/ci/v7_ci_capacity.py` adds failed/errors/xfailed/xpassed/deselected counts,
fixed failure codes and up to 100 sorted unique failed repository test IDs.
IDs are matched against names derived from the local backend test source AST,
without executing or importing test modules. Unknown paths/functions, absolute
paths, traversal, URL strings and dynamic parameter values are not published.
Collection errors can identify only a known test file. Only stdout summary
FAILED/ERROR lines are inspected; traceback/error message/DSN/stderr remain private.
`-rfsE` ensures failed/error summary lines exist instead of requesting only skips.

Exit codes distinguish test failure, collection/interruption, pytest internal,
usage, no tests, unexpected exit and summary/count rejection. Timeout publishes
only TIMEOUT and ignores all partial output. Setup/temp-cleanup errors use a fixed
code. Temp cleanup, original 900s timeout, environment stripping, FAIL acceptance
rules and aggregate gating are unchanged. No workflow or product code changed.

## Tests

- Regression first: 15 failures for absent diagnostics.
- Targeted diagnostics + existing capacity tests: **35 passed**, 8.62s.
- Adversarial synthetic fixtures cover email/token text inside parameter IDs,
  unregistered filenames/functions, traversal, absolute paths/URLs and timeout
  partial output. Valid repository IDs are preserved without raw descriptions.
- Added explicit no-import/class-method test: final diagnostics **16 passed**,10.32s.
- Full scripts/ci: **291 passed, 2 failed**,146.21s. Both failures are existing
  `test_v7_schedule_runtime_gate.py` expectations for four schedule proofs while
  the supplied base pins six. Root owns correction; this branch does not alter
  those assertions or the PostgreSQL runner. This is not a full CI PASS.
- `git diff --check`: PASS. No backend suite, live provider, PostgreSQL or GitHub
  rerun performed by this task.

Commands use D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8,
TEMP/TMP and explicit pytest basetemps on D. Git Bash selected for all-CI tests.

## Limits and safety

AST discovery omits generated/imported/inherited test names not statically declared
in the test module; failure code/counts remain available even without a name.
Source files and test identifiers are repository-trusted; this is not protection
against intentionally malicious repository code. Human-sensitive test parameter
IDs are discarded. Reported summary counters cannot replace runtime proof and
do not diagnose an individual assertion or thread exception.
No push, merge, deploy, production access, secret changes or test weakening.
