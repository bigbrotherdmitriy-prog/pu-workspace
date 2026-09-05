# MVP1–MVP4: parallel hardening checkpoint

Date: 2026-09-05

Branch: `codex/mvp1234-wave2-integration`

Base: `3ff8b767383598226abe7fb3d6710e2de3bf734c`

## Scope and decision

This checkpoint closes bounded local implementation gaps; it is not a claim
that the entire technical specification or production acceptance is complete.
Production, real mailboxes, provider documents, credentials, main dirty worktree,
merge, push and deployment were not used or changed.

Three independent worktrees were implemented in parallel and integrated locally:

| Work | Source SHA | Integrated SHA |
| --- | --- | --- |
| Gmail paged resync | `8c3472a511639c343b58acc197fddfca51515f7f` | `8f093c5cd097131a8f57a68dd4ce95d88eabf2f2` |
| Owned PostgreSQL runtime gates | `1ba5c6127cc0270e9ccc664c13193a9c2993c96b` | `5353df3cdb8d28b5d2782d22d29967c2a4e229fe` |
| Cleanup worker fencing | `0c92466bb26bde19dd7f6271031460ad663e9635` | `54b33a1e52ff028fbd47e2b286df555b9576e67c` |

The parent added:

- `576c9b86e13980e5408dcb85a057dc0daf7e2165`: cleanup API preserves the worker's
  actual boolean proof, never manufactures `originals_affected=false`. Unknown
  proof is null. The UI continues polling while running but requires explicit
  false and an integer count at completion. Eight backend RED cases and one UI
  RED scenario preceded the fix; backend/API checks then passed.
- `d06cfd7bec56d26e503cc1b5d31c644538bed8e8`: real PostgreSQL finance concurrency
  test definitions with actual project membership, competing confirmation and
  correction transactions, exact fact/audit counts and CAS. Dedicated test DB
  and current migration required; no banking action is performed. Storage PG
  fixtures allow the `postgres` service hostname only inside GitHub Actions.

No textual cherry-pick conflicts occurred. The only migration head remains
`a54f001c0a18`, matching `CURRENT_SCHEMA_REVISION`; no migration was added.

## What is now implemented

- Gmail reads at most 100 references per page and ingests each page separately.
  More than 100 messages no longer blocks resync. Limits are 100 pages and
  10,000 raw references per run, including duplicates. Profile history is pinned
  before scanning; the cursor advances only after all pages succeed. Partial
  failures retry from page one and reuse mailbox-scoped ingestion deduplication.
  Guard checks apply after provider reads and before ingestion, including a
  404-to-resync epoch transition. See [Gmail report](mvp2-gmail-paged-resync.md).
- Cleanup checks exact job/worker/attempt/lock timestamp/lease/cancellation at
  entry, before effects and before receipt commit. Fresh requester role, project
  binding, connection and original-folder guards apply per item. Receipts pin
  job/version/attempt. Static capability denial occurs before client or token
  resolution. Live provider capabilities remain disabled. See
  [cleanup report](mvp1-cleanup-worker-fencing.md).
- CI owns and migrates separate MVP1–MVP4 test databases, invokes exact test
  node IDs and fails on mandatory skip, missing count, xfail or deselection.
  A failure cleaning one owned DB does not prevent cleanup of the remaining
  owned databases. The JSON protocol distinguishes PASS/FAIL/SKIPPED/INCOMPLETE/
  ERROR/NOT_RUN and contains no raw child output. See
  [runtime report](mvp-runtime-owned-postgres.md).

## Integration validation

Product code under test: `54b33a1e52ff028fbd47e2b286df555b9576e67c`.
Frontend was already final at `576c9b8`; subsequent commits do not change it.

| Check | Result |
| --- | --- |
| Full backend | 1610 passed, 27 skipped, 33 warnings; 510.48 s |
| Full frontend | 208 passed |
| Frontend TypeScript / build | PASS; existing large-chunk warning remains |
| Full Chromium E2E | 30 passed, 44.4 s; synthetic API fixtures |
| Full CI Python and Git Bash mock contracts | 183 passed, 101.05 s |
| Alembic heads / schema constant | PASS, sole `a54f001c0a18` |
| Root cleanup API and finance fixture/URL tests | 14 passed, 2 real-PG skips |
| Root cleanup polling | 6 passed |
| Branch Gmail targeted | 60 passed; adjacent suite 65 passed, 1 PG skip |
| Branch cleanup targeted | 97 passed; 16 existing cleanup passed |
| Docker / PostgreSQL execution | NOT_RUN locally; executables/test DB unavailable |
| Live Google / Yandex / Gmail | NOT_RUN |

The 27 backend skips comprise 24 PostgreSQL checks without explicitly configured
isolated databases, one OCR benchmark without local Tesseract and two filesystem
symlink checks unavailable on this Windows host. None are counted as PASS.
The 33 warnings concern Alembic's existing `path_separator` configuration.

Read-only GitHub preflight succeeded: the configured remote matches
`bigbrotherdmitriy-prog/pu-workspace`, and the integration branch is not yet
published. No push or workflow dispatch was performed. Generated frontend build
assets were removed/restored to their tracked state; browser evidence stays in
the local ignored test cache. The original dirty worktree's status was preserved.

Python commands use the existing workspace `.venv-pu-workspace-tests` executable
with `-X utf8` and task-specific `--basetemp`; shell contracts use existing Git
Bash first on PATH and `PYTHONUTF8=1`. Core test commands:

```powershell
# cwd: backend
python -X utf8 -m pytest tests -q -rs --tb=short --basetemp=.pytest-mvp-final-hardening-full
python -X utf8 -m alembic -c alembic.ini heads
# cwd: repository root
python -X utf8 -m pytest scripts/ci -q --tb=short -p no:cacheprovider --basetemp=.pytest-mvp-final-hardening-ci
# cwd: frontend
npm.cmd test -- --run
npm.cmd run check
npm.cmd run build
npm.cmd run test:e2e
```

## Remaining acceptance blockers

1. Actual isolated PostgreSQL/CI execution for this exact final SHA, including
   migrations, concurrency and process fault scenarios. Local SQLite/contract
   passes do not establish these results.
2. Live managed-copy ownership, complete tree replay/reconciliation, descendant
   original protection, remote preconditions and mutation safety. Worker fencing
   cannot atomically cancel an already-started external provider request.
3. A stable Gmail backlog exceeding 10,000 references still requires partitioned
   or resumable recovery; retries alone do not solve permanent budget exhaustion.
   Live mailbox and attachment worker recovery remain separate gates.
4. New finance PG tests cover human confirmation/correction only, not supply
   concurrency, backup/restore or owner/legal decisions on currency/VAT/retention.
5. Dedicated authority, materialization/local-upload and generic PostgreSQL
   fixtures outside the bounded MVP runner remain explicit acceptance gaps.

The release decision remains **CONDITIONAL** until the relevant runtime, live
provider and owner/legal gates are evidenced. Safe local defaults are retained;
no forbidden live capability was enabled to manufacture a PASS.

## Next externally authorized run

After authorization for the final SHA, push the integration branch without force.
Both existing isolated runtime workflows include this branch. Inspect resulting
run headSHA and JSON protocol; do not treat an older run as evidence for this SHA.
Do not merge or deploy as part of validation.
