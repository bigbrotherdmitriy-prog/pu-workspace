# v7 CI capacity split

Date: 2026-09-08. Branch: `codex/v7-ci-capacity-wave3`.
Base: `848caabf8727a6657f8f2a9d2330bde95036dfed`.

## Scope / outcome

The full offline backend suite no longer consumes the serial PostgreSQL runner's
21-minute work budget. It runs concurrently in a separate `backend-offline` job,
without a PostgreSQL service, production environment file or inherited provider
credentials. Its existing 900-second subprocess limit was preserved; the job has
a 17-minute runner step and 22-minute outer ceiling.

The PostgreSQL runner retains all **37 exact mandatory proof nodes**, A/B/C,
corpus, process faults, owned-database cleanup and the fixed 1320-second budget
(1260 work + 60 cleanup). Alembic stays at `a54f001c0a20`. Its protocol explicitly
states `offline_backend=SEPARATE_JOB_NOT_ASSERTED_HERE`, rather than synthesizing
an offline PASS. No queue, product, frontend or migration code changed.

`acceptance-gate` depends on `runtime`, `backend-offline`, `local-engines`, and
`lint`, with `if: always()`. Every result must be exactly `success`; missing,
malformed, failed, skipped or cancelled dependencies fail the gate. Upstream
artifact publication is part of each job; missing protocol files fail upload.
The aggregate success describes isolated CI only, not complete product acceptance.
The existing workflow also accepts the `codex/v7-execution-wave3` integration branch.

## Safety

- Offline child environment uses an OS-variable allowlist. Test/database/provider
  flags and credentials are not inherited. SQLite is explicit; application test
  secrets are freshly generated in memory. No key is put in artifacts or logs.
- Pytest stdout/stderr are captured in memory. Only allowlisted counters, scope,
  durations and statuses enter the offline protocol. Timeout partial output and
  filesystem-publication errors do not print raw diagnostics.
- Conditional offline skips remain counted and are **not** PostgreSQL acceptance.
  Mandatory PostgreSQL and local-engine jobs still enforce their separate proofs.
- Final-gate input arrives through an environment value, not shell interpolation;
  malformed JSON fails with a safe protocol. Only expected job names/statuses are
  emitted. Invalid commit identifiers are replaced with `local`.
- Protocols: `v7-backend-offline-artifacts/protocol.json`, existing
  `v54-runtime-artifacts/protocol.json`, and `v7-ci-capacity-artifacts/protocol.json`.
  GitHub artifacts are named by run ID and attempt and retained for seven days.

## Verification history

Before implementation: **11 regression failures** for missing parallel job,
environment isolation, counters and aggregate gate.

The initial C: disk-full incident interrupted an edit and truncated this fork's
runner. After relocation to D:, that file was restored from its own HEAD through
`apply_patch`; `git diff --numstat` confirmed no difference before intentional edits.
The original integration worktree was not modified. Only the author's completed
old pytest scratch directories (~1.4 MB) were deleted; no client data was removed.

Targeted initial contracts: **41 passed**. First complete CI suite: **259 passed,
1 failed**; the existing Gmail-history assertion still expected environment
blanking inside the removed inline phase. It was replaced by a behavioral check
that the separate offline child excludes the supplied history DSN and uses SQLite.
Targeted final capacity cases: **20 passed**, including safe publication failure.
Complete CI rerun: **260 passed in 128.12 seconds**. After adding the publication
failure regression, the final complete suite was rerun again: **261 passed,
zero skips, in 163.78 seconds**. `git diff --check`: PASS. Workflow YAML and
branch/needs/artifact contracts passed; standalone actionlint was not found in PATH
and was not executed locally. Its existing CI job remains mandatory.

Commands (isolated worktree on D:):

```powershell
$env:TEMP = 'D:/PU-Workspace/tmp'
$env:TMP = $env:TEMP
$env:PATH = 'C:/Program Files/Git/bin;' + $env:PATH
& 'D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe' -X utf8 -m pytest scripts/ci -q --tb=short --basetemp=D:/PU-Workspace/tmp/capacity-suite-final-261
git diff --check
```

## Limits / next gate

**CONDITIONAL — GitHub/Docker/PostgreSQL runtime NOT RUN.** This change removes one
900-second phase ceiling from the serial critical path, but does not predict actual
CI latency, promise sufficient runner capacity, or claim full backend execution on
GitHub. Hard cancellation/network/filesystem failure can still prevent artifacts;
the aggregate gate will not convert missing results into PASS.

Offline subprocesses run tests using synthetic fixtures, not a network sandbox.
Live provider validation is explicitly NOT_RUN. No real credentials are supplied.
The final gate does not download and independently authenticate artifact contents;
it requires successful producer jobs and artifact publication for the same workflow.

No push, merge, PR, production deploy, backend product test run or frontend build was
performed for this CI-only package. After integration, run the existing isolated
workflow on the authorized integration SHA; do not deploy on this contract result.
