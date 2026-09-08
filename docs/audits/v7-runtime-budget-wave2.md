# v7 runtime budget: bounded failure, not runtime acceptance

Date: 2026-09-08. Base: `4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.
Branch: `codex/v7-runtime-budget-wave2`.

## Evidence and scope

The runtime job allows 28 minutes including setup, dependency installation and
artifact upload. The serial subprocess ceilings total 7380 seconds (123 minutes):
8 migrations × 180, main PostgreSQL groups 1980, remaining groups 1500,
schema fixture 180, full backend 900, A/B/C 900, corpus 120, gzip harness 180,
process fault 180. These are ceilings, **not estimated execution time**.
The recorded preceding local backend run took 592.09 seconds; its existing
900-second phase limit was not demonstrated to be insufficient.

Outer cancellation could previously interrupt the Python `finally` block before
safe protocol creation. This change does not hide slow phases or remove coverage.

## Change

- Fixed monotonic runtime budget: 1320 seconds, including a 60-second cleanup reserve.
- Work deadline is 1260 seconds from `main()` entry. Each subprocess receives the
  smaller of its unchanged phase ceiling and the work remainder.
- No child starts after expiration. The attempted phase records safe `ERROR`;
  subsequent mandatory phases remain `NOT_RUN`, and overall result is `FAIL`.
- `main()` clears old phase records and resets deadlines even after protocol write
  errors. Failed cleanup retains owned database IDs; it is not reported as PASS.
- Admin connection timeout remains 5 seconds. Admin SQL now has a 5-second statement
  and 1-second lock ceiling. Cleanup uses 1-second statements and checks its deadline
  before each owned database; normal bounds cover 15 databases × two statements.
- Head verification uses bounded 1-second SQL/lock waits.
- Workflow runner step is bounded to 24 minutes within the unchanged 28-minute job.
  The nominal 2-minute difference after the inner budget is safety slack, not a claim
  that installation, cancellation or artifact delivery has a guaranteed duration.
  Setup is included in the 28-minute job; a full 24-minute step fits only if setup
  stays below 4 minutes, with additional time needed for verification and upload.
- No environment variable can increase the internal budget. No phase/node removed,
  no raw subprocess output emitted, no production resources touched.

## Tests

Regression before implementation: four new tests failed (after using a worktree-local
pytest temporary directory; the initial default-temp attempt also hit Windows ACL).
After implementation: seven new budget tests passed. The first full CI run was
231 passed / 3 failed in 95.69 seconds: three existing Bash smoke tests decoded the
Cyrillic worktree path using Windows' default codepage. No assertions were weakened;
the full suite was rerun with Python UTF-8 mode, matching the integration baseline.
Final complete `scripts/ci` suite: **234 passed, no skips, in 98.16 seconds**.
`git diff --check`: PASS. YAML is exercised by the suite; standalone actionlint,
Docker and PostgreSQL runtime were unavailable and are not reported as PASS.

Cases cover remaining-budget clipping, no child after expiration, safe ERROR/NOT_RUN,
reused `main()` cleanup and state reset, nested workflow budgets, retained owned IDs
on cleanup expiration, SQL ceilings, and suppression of partial timeout output.

Commands from this worktree:

```powershell
$env:PATH = 'C:/Program Files/Git/bin;' + $env:PATH
& '../.venv-pu-workspace-tests/Scripts/python.exe' -X utf8 -m pytest scripts/ci -q --tb=short --basetemp=.pytest-budget-suite-utf8
git diff --check
```

## Remaining limitations / decision

**CONDITIONAL: isolated runtime has not been executed.** A 1320-second budget cannot
guarantee completion of serial phases whose summed ceilings are 7380 seconds. An
exhausted budget intentionally fails with incomplete coverage. Future capacity work
should split independent offline and owned-PostgreSQL jobs based on measured CI
times, not increase every deadline or declare skipped tests passed.

OS/process hard kills, stalled filesystem writes, server/network failure and GitHub
job cancellation can still prevent a final artifact. `subprocess.run` bounds its
immediate child, not all descendants; service teardown remains the final isolation
boundary. Database cleanup timeouts produce FAIL and leave explicit owned IDs;
they are not a production cleanup mechanism.
The 5-second CREATE DATABASE ceiling may fail on a slow runner and must be reported
as infrastructure failure, not converted into skipped acceptance or PASS.

No product code, migrations, frontend, main dirty worktree, secrets, live providers,
push, merge, PR or deploy were changed/performed. Backend and browser suites were not
rerun because only CI orchestration, its tests and this report changed.
