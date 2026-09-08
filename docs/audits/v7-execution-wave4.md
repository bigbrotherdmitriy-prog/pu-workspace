# v7 execution wave4: snapshot, exact draft review, meeting faults

Date: 2026-09-08. Branch: `codex/v7-execution-wave4`.
Base: `8794e1feed61ad5c8fd8c462e0d4e2059bfb7ac1`.
Decision: **CONDITIONAL**. This is not acceptance of all MVP1–9.

## Integrated commits and ownership

| Integrated | Source | Scope |
| --- | --- | --- |
| `590bf00` | `8811294` | Exact draft content review, current role and stale token rejection |
| `7981ee1` | `f587050` | Actual meeting confirmation before/after commit process faults |
| `26c830b` | `83257d0` | Exact reviewed token required before Gmail enqueue |
| `6bd7f11` | `7cc3f34` | Completed wave3 regression report only |
| `fe1bf86` | `8a4c41c` | Snapshot lease-owner fencing and recovery harness |
| `338ebd0` | `ddcd445` | Independent bounded snapshot runtime workflow |

No cherry-pick conflicts. Root owns App, review card, actual-App browser tests,
existing CI pin additions and this integration report. Streams used separate D:
worktrees. No models or migrations added; sole head and schema constant remain
`a54f001c0a20`.

## Product changes

- Snapshot publication verifies exact job, worker, attempt and claim timestamp;
  a superseded delivery cannot overwrite a recovered result. Provider metadata
  I/O is outside explicit row locks; ownership and binding are checked again
  before publication. Reading bytes/copying originals is not part of this handler.
- Draft review is explicit: load, edit, save, separately approve, separately send.
  A review fingerprint binds exact content/context; it is not a permission token
  or a new provider approval. Stale or missing fingerprints fail closed.
- App hydrates inbox tokens only when canonical draft content/status/recipient
  match. Wrong-project inbox rows are hidden. Late review/send callbacks do not
  publish old-project notices. The card rejects success with different content.
- Queued send is displayed as queued, not delivered. `sending` is explicitly
  unconfirmed. No real mail was sent. Corrective follow-ups retain their existing
  separate confirmation path.
- Five meeting process-fault nodes are added to the existing mandatory PostgreSQL
  phase. Original 37 proofs are preserved (42 total), with unchanged deadlines,
  schema head and fail-closed gate.
- Snapshot crash/recovery has a separate workflow to avoid silently exhausting
  that serial runtime budget. It uses a guarded empty owned database, bounded
  process groups, strict safe JSON and cleanup. It is **not yet a dependency of
  the original aggregate gate**: both workflows must be checked independently.

## Verification

- Full scripts/ci rerun: **278 passed**, 150.70 seconds, no skips. First invocation
  exposed a stale exact branch allowlist plus local WSL bash selection; branch
  expectation was updated explicitly, Git Bash selected via process PATH. No
  test was skipped or relaxed.
- Full frontend including final ABA addition: **323 passed**, 32 files, 29.33 seconds.
- Draft card targeted regression: **8 passed** before ABA test. A new malformed
  response test initially counted the legitimate load callback; its spy is now
  reset before testing mutation, preserving the no-unreviewed-update assertion.
- Actual-App Chromium synthetic review/save/approve/send and conflict: **2 passed**,
  13.4 seconds. Requests were intercepted; no real provider effect occurred.
- TypeScript: PASS, including final scope/ABA additions.
- Vite build to D:/PU-Workspace/tmp/wave4-build-final: PASS, 2.08 seconds; existing
  chunk >500 kB warning retained. Tracked react_dist untouched.
- Full backend is running on the integrated backend snapshot; result is pending.
  Do not infer PASS from the independent targeted suites.
- Alembic heads: one `a54f001c0a20`; CURRENT_SCHEMA_REVISION matches.
- Docker CLI discovered locally but `docker version` returned exit 1; server
  availability was not established. No Docker containers or PostgreSQL were run.
- Standalone actionlint not available. YAML and workflow contracts are covered
  offline; this does not replace actionlint or runtime.

## Still not proved / not included

1. Real PostgreSQL locking, meeting crash/replay and snapshot lease recovery.
2. API OS-process restart / complete Compose restart with the actual snapshot.
3. Live Google/Yandex/Gmail acceptance; no user data or credentials used.
4. Existing snapshot enqueue holds a project lock across provider metadata I/O;
   provider credential generation is not frozen by this bounded fix.
5. Draft fingerprint is exact-state, not monotonic history (identical ABA state
   can yield the same hash). Current authority remains mandatory.
6. Other legacy clients need review tokens. One invalid-context draft can make
   the scoped list deny; this is documented fail-closed behavior, not a bypass.
7. Snapshot workflow hard cancellation cannot guarantee its cleanup step runs;
   owned runner/service isolation remains necessary. Child SQL timeout options
   are stripped by existing harness environment; outer process group is bounded.
8. Whole-MVP acceptance, WBS, graph row lifecycle, unified DDS views and remaining
   later-MVP criteria are not closed by this wave.

## Reproduction and safety

From backend: `python -X utf8 -m pytest -q --basetemp <unique-D-test-directory>`.
From repository: `python -X utf8 -m pytest scripts/ci -q`, with Git Bash on PATH.
From frontend: `vitest run`, `tsc --noEmit`, and
`playwright test e2e/response-draft-review.e2e.ts` using the pinned dependencies.
All outputs/caches were directed to D:, except existing installed Chromium.

Original dirty worktree tracked diff remains
`090283159baf1bf90bad900ec2366c2bd89ce01b`. No push, merge, PR, production deploy,
secret changes, real emails, original document edits or global Docker cleanup.
See [operator guide](../user-guide/v7-reviewed-workflows-ru.md).

Future publication (not executed): push the explicitly selected candidate branch,
then run both `v54-pilot-runtime.yml` and `v7-snapshot-recovery.yml` on that branch.
Neither an offline test nor a workflow definition is evidence of runtime success.
