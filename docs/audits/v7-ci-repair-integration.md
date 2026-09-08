# v7 CI repair and parallel execution integration

2026-09-08. Branch: `codex/v7-ci-healthcheck-fix`, D: worktree.
Base: `fbbf12c7c5913874af1a02332724a1d23927e0a5`.

## Included work

1. `ac7c197`: snapshot service quoting, RED then 27 targeted tests PASS.
2. `148fe88`: stale schedule contract expected four instead of six mandatory
   PostgreSQL cases. Full CI initially 286 PASS / 2 FAIL; nine corrected contract
   tests PASS. Four/five/seven results are rejected, exactly six required.
3. `872dfe6` (source `bb86ac5`): backend/frontend independent jobs, preserved
   `test-and-build` aggregate requiring both success. No commands removed.
4. `679b6bf` (source `46d5d2a`): safe offline failure counts and source-registered
   test identifiers. Dynamic parameters, stderr and document content excluded.
5. `b8e7a4f`: strict authority failure sentinel adapter (exact keys and enums),
   28 adapter/existing runner tests PASS.
6. `fae8c3a` (source `de6124d`): frozen fixture time reproduced the authority
   timestamp invariant error; advance fixture clock by one microsecond. No
   production model/permissions change. Thread failures captured safely.

Independent parallel tasks covered CI acceleration, offline diagnostics and
authority diagnosis. Root integrated and reviewed their changes. No conflicts.

## Verification

- Integrated CI before authority adapter: 321 passed / 116.26 seconds.
- Final CI suite: **333 passed**, no skips / 144.37 seconds.
- Authority tests: 14 passed, 2 PostgreSQL skipped / 23.22 seconds.
- YAML parse and git diff check: PASS.
- Alembic: single unchanged `a54f001c0a20` head.
- No frontend/product implementation changed; frontend suite not repeated.
- actionlint not available locally; no installation performed.
- D: used for new worktrees, Python and test temporary directories.

## Published base evidence, not evidence for this new candidate

- GitHub run 34205918716: Docker smoke PASS, including cleanup.
- GitHub run 34205918556: main backend/frontend CI PASS.
- GitHub run 34205918743: snapshot container initialization FAIL, health quoting.
- GitHub run 34205918687: authority and offline FAIL; other remaining runtime
  phases not run. Offline failure cause not present in the old safe protocol.

## Remaining conditions

**CONDITIONAL**: new GitHub/container/PostgreSQL run required. The reproduced
fixture cause is not a conclusive reconstruction of the old thread exception.
Production same-timestamp authority updates remain a separate edge case for
review; fail-closed guards were not changed. Actual speed improvement has not
been measured; parallel jobs remove serial dependency but depend on runner
availability. Offline failure still requires the next diagnostic result.

### First rerun after `2a0bf78`

The authorized fast-forward push triggered four isolated workflows. Docker
Compose smoke `34209315435` passed. Snapshot run `34209315403` now initialized
its PostgreSQL service, checked out the repository, installed dependencies,
migrated the owned database and cleaned it up, but the child fault harness
failed. Its safe artifact reported only `phase=snapshot_fault`,
`runtime=NOT_RUN`, `cleanup=PASS`; the outer coordinator discarded the child's
already allowlisted subphase.

The follow-up adds a strict exact-key/enum parser for that child subphase.
Untrusted keys, values and types are rejected, stderr remains unpublished, and
no raw child output is retained in the protocol. Targeted snapshot tests:
**29 passed**. This is diagnostic only and requires a separate authorized push.

The other rerun results are now complete:

- Main CI `34209315364`: PASS. Independent frontend, backend and preserved
  `test-and-build` aggregate all passed.
- Docker smoke `34209315435`: PASS.
- v54 runtime `34209315402`: FAIL only at the final database cleanup. All 26
  recorded phases passed, including 15 mandatory PostgreSQL groups, 302 A/B/C
  integration tests and process-fault. The safe protocol recorded
  `cleanup=FAIL`; it did not identify the remaining owned database.
- Offline in the same runtime: 2421 passed, 1 failed, 55 skipped. The new safe
  diagnostic identified
  `test_mvp3_meeting_binding_migration.py::test_meeting_binding_migration_is_sequential_and_preserves_unbound_legacy`.

That migration test inherited the offline suite's SQLite URL while generating
PostgreSQL-specific DDL. It now pins the PostgreSQL dialect without connecting
to a database. The failure reproduces locally before the correction and passes
after it. Runtime cleanup now uses PostgreSQL 16 `DROP DATABASE ... WITH
(FORCE)` after terminating sessions, raises its per-statement bound from one to
three seconds, retains the outer deadline and exact owned-name allowlist, and
reports only allowlisted database names still remaining. Targeted migration,
runtime, cleanup and ownership tests: **46 passed**.

Final integrated CI contract suite after these follow-ups: **335 passed**, no
skips / 144.90 seconds. This does not replace the required GitHub PostgreSQL and
container rerun.

### Second rerun after `a2891e6`

Docker smoke `34212914501` passed. Snapshot `34212914676` passed service
initialization and database cleanup, then reported `child_phase=guard`. Static
inspection found that the synthetic `DriveConnection` fixture omitted the
model's required non-null `account_email`. The coordinator kept its phase at
`guard` through imports/schema/fixture setup, obscuring this distinction.

The fixture now supplies an `.invalid` synthetic mailbox identity through a
small factory tested without provider access. Early phases are split into
`imports`, `schema` and `fixture_seed`, all enforced by the outer exact enum.
Snapshot/durable targeted tests: **26 passed**. No real mailbox, provider or
document is used.

### Third rerun after `b1c6882`

Docker smoke `34214357156` passed. Snapshot `34214357229` reported
`child_phase=schema`; the previous required-identity fix therefore passed.
The fresh migration intentionally creates exactly one organization named
`PU Workspace`, while the harness incorrectly required the organization table
to be empty. The fixture now accepts exactly that migration-owned row and
refuses zero, multiple or differently named rows. It does not accept customer
or production data. Targeted snapshot/durable tests: **27 passed**.

The remaining `a2891e6` workflows completed after the local fixture fix was
prepared:

- Main CI `34212914582`: PASS (backend, frontend and aggregate).
- v54 PostgreSQL runtime `34212914610`: PASS (runtime, backend-offline, local
  engines, lint and fail-closed acceptance gate).
- Docker smoke `34212914501`: PASS.
- Snapshot `34212914676`: FAIL at the now-fixed synthetic fixture setup.

Therefore the only red workflow for that published SHA is the isolated snapshot
fixture. The new local HEAD still needs its own GitHub execution before the
overall status can become PASS.

Push only after separate authorization of the resulting HEAD:

```powershell
git push origin HEAD:refs/heads/codex/v7-execution-wave6
```

This fast-forward destination already triggers isolated main, smoke, v54 runtime
and snapshot workflows. Do not merge or deploy. No push performed here.

Original dirty worktree tracked diff fingerprint remains
`090283159baf1bf90bad900ec2366c2bd89ce01b`. No production resources, credentials
or customer data accessed or modified.
