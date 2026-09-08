# v7 execution wave 2 — integration record

Date: 2026-09-08. Branch: `codex/v7-execution-wave2`.
Base: `4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.

## Scope

Continuation requested by the user, with parallel isolated worktrees. No main
dirty worktree, production, secrets, live accounts, push, merge or deploy touched.
This is not acceptance of the whole MVP1–9 specification.

Original integration remains at `5226782908dff519a2bf2c6b9d320b59f2972c10`;
its tracked dirty-diff hash is unchanged:
`090283159baf1bf90bad900ec2366c2bd89ce01b`. The wave-1 checkout is still clean at
`4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.

## Integrated packages

| Package | Source SHA | Integration SHA | Evidence |
| --- | --- | --- | --- |
| Schedule API rejects unsupported intent instead of silently discarding it | `890b81a6f6be0590981c28fa33628aafb8112c0b` | `88f5a2a` | Source scoped suite 291 passed; 12 failures reproduced before fix |
| Meeting binding can use independently authorized retained XLSX child | `900942ac226847e6c94444b350763e33b342fd34` | `c8dca79` | Source scoped suite 133 passed/1 PG skip; final new 18 tests passed |
| Fixed runtime work deadline and cleanup reserve | `8a48b85bfffd65f3cd14d14e280b602ea84082f4` | `1d2b4a2` | Source full scripts/ci 234 passed, no skip |
| Persisted calendar-day graph API and additive schema | `321aab9a9948f5053f0bd97477bd3a00ab7ce1cb` | `ddff4dd` | Source scoped suite 320 passed/4 PG skips |
| Reject oversized remapped clone dependencies atomically | `c996c9b016a708e65998116859af39c2b736bb07` | `ac97dbb` | Real high-ID regression reproduced, final source scoped 40 passed/4 PG skips |

Integrated boundary check: 50 passed in 14.56 seconds (meeting retained evidence,
existing meeting source binding, schedule API boundary). These tests preceded the
schedule persistence package; they are not a final whole-candidate result.

Final integrated CI/harness: **241 passed**, 140.63 seconds, Python 3.12 with
`-X utf8 -m pytest scripts/ci -q --tb=short` and Git Bash available. No skips.
Final full backend: **2271 passed, 50 skipped, 38 warnings** in 675.88 seconds
(11:15), Python 3.12.14, `-X utf8 -m pytest -q --tb=short`, with unique basetemp
`.pytest-v7-wave2-verified-full`. Warnings concern Alembic's legacy path separator.
Skipped cases are not acceptance evidence. Decision: **CONDITIONAL** until the
isolated PostgreSQL/Docker runtime and required browser/live scenarios execute.
No frontend or live-provider tests were run in this wave.
An earlier backend invocation was intentionally interrupted to incorporate the
clone-boundary fix and is not reported as a successful full run.
The subsequent full invocation exposed a historical meeting-migration test that
still assumed CURRENT_SCHEMA_REVISION's parent was a18. The original a19→a18
assertion was retained on the explicit a19 revision, and the new a20→a19 link was
added independently. Six schema/migration checks passed; the full run was restarted
after this test-only correction. No migration or product assertion was weakened.

Schema is now a single sequential head `a54f001c0a20` after `a54f001c0a19`.
CLI Alembic heads confirmed this. Current schema/readiness/runtime assertions
were advanced to a20; historical a18→a19 migration coverage was preserved.
No applied migration was rewritten. Real PostgreSQL upgrade remains unexecuted.

Four new mandatory PostgreSQL nodes cover graph CAS, legacy upgrade/safe downgrade,
and refusal to discard active or legacy graph intent. They use the existing owned
MVP4 test database and private UUID schemas. Missing/skipped/incorrect proof counts
fail the gate. This adds 300 seconds of possible phase budget, not measured runtime;
the overall deadline remains fixed and may fail incomplete acceptance honestly.

## Meaning and remaining boundaries

- The meeting path checks exact child pins, current source/authority/lifecycle,
  and each requested evidence independently. It does not claim purged original
  bytes exist, perform file reads, or enable other unsupported representations.
- Synthetic staged XLSX → purge original → bind meeting → proposal → human
  confirmation → one Task/replay is covered. PostgreSQL concurrent purge and
  HTTP/browser acceptance remain separate. Existing pending meeting UI was not
  copied out of the dirty original checkout.
- Runtime work has a 21-minute budget plus one-minute cleanup reserve, below a
  24-minute runner step. Missing phases remain NOT_RUN/FAIL. Hard cancellation,
  network or filesystem stalls can still prevent cleanup/artifact publication;
  the budget is not a guarantee that all tests fit or that runtime passes.
- Worktree-specific source suites must not be summed as unique tests or treated
  as a substitute for the final integrated runtime.
- Graph GET/PUT persists a complete existing-ID draft graph, explicit anchor and
  duration/constraint intent, calculated dates, and true graph_revision CAS. Clone
  remaps dependencies without actual facts; legacy writers invalidate revision;
  approval checks exact graph revision and stored graph consistency.
- Migration keeps legacy duration/milestone unknown, without invented dates.
  Downgrade locks both schedule tables before checking all new intent fields and
  refuses data loss. This is not a production rollback drill.
- Graph UI, WBS, working calendars, graph-aware row insertion/deletion and durable
  request replay are not implemented by this package. Stale PUT returns 409.

## Completion forecast

The remaining backlog still includes functional integration, real PostgreSQL and
fault acceptance, browser/live-provider validation, deployment recovery, and
owner/legal inputs. No elapsed-time measurement justifies promising that the whole
specification will finish today. Independent code work continues without waiting
for those external decisions, but their acceptance status cannot be fabricated.

## Handoff

This branch contains the ordered wave-2 commits on top of the clean wave-1 SHA.
Do not replay both source SHAs and their integration equivalents. On another
compatible clean checkout already at wave 1, the transfer range is:

```powershell
git cherry-pick 4bcc94ab113f544a88ed9eaeb7ebd37b6133b410..codex/v7-execution-wave2
```

Publication was not performed. A future authorized
`git push -u origin codex/v7-execution-wave2` triggers the isolated runtime workflow
through its explicit branch filter. Do not also dispatch a duplicate run blindly.
Frontend integration, live providers, production migration and deployment remain
separate work, not consequences of this local commit.
