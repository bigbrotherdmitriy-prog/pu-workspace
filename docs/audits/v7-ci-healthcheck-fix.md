# v7 CI service healthcheck correction

Date: 2026-09-08. Base: `fbbf12c7c5913874af1a02332724a1d23927e0a5`.

GitHub run `34205918743`, job `101995243804`, failed during container
initialization, before checkout. Docker reported `unknown shorthand flag: 'U'
in -U`. The service options enclosed a multi-argument health command in single
quotes, which did not preserve it as one argument in the runner invocation.
Use double quotes, matching the repository's other PostgreSQL service jobs.

Regression: `test_service_healthcheck_contract.py` failed against the original
workflow (1 failed, 9 passed). After correction, this test and the existing
snapshot workflow tests pass: **27 passed**, no skips.

Command (Python from the D: test environment):

```powershell
$env:TEMP='D:/PU-Workspace/tmp'
$env:TMP=$env:TEMP
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -m pytest scripts/ci/test_service_healthcheck_contract.py scripts/ci/test_v7_snapshot_workflow.py -q --basetemp=D:/PU-Workspace/tmp/healthcheck-fix-20260908-a --tb=short
```

An earlier local attempt hit a permission error in the default C: pytest temp
directory (21 passed, 6 setup errors); the D: temporary directory resolved this
environment issue. No assertions were removed. The new test is a static
argument-format contract, **not** a Docker runtime proof.

Other runs of the base SHA: Docker smoke `34205918716` and main CI
`34205918556` succeeded. Runtime `34205918687` failed on authority concurrency;
its offline job also failed without sufficient safe failure detail. Those are
separate investigations, not fixed by this change.

Status: **CONDITIONAL**, pending another isolated runtime run. Product code,
production and the original dirty worktree are unchanged. No push, merge or
deployment performed for this correction.
