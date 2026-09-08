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

Push only after separate authorization of the resulting HEAD:

```powershell
git push origin HEAD:refs/heads/codex/v7-execution-wave6
```

This fast-forward destination already triggers isolated main, smoke, v54 runtime
and snapshot workflows. Do not merge or deploy. No push performed here.

Original dirty worktree tracked diff fingerprint remains
`090283159baf1bf90bad900ec2366c2bd89ce01b`. No production resources, credentials
or customer data accessed or modified.
