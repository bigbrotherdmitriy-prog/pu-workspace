# v7 WBS graph-aware import preview

Date: 2026-09-08. Base: `73178e0db3be8f4f2aadde339c17725b605e88be`.
Branch: `codex/v7-wbs-import` in an isolated worktree on `D:`.

## Audit and boundary

The current `backend/app/structured_import.py` recognizes a flat schedule title,
start, finish and progress. It intentionally does not represent hierarchy,
duration, milestones or dependency links. The persisted graph rows endpoint can
atomically create/update/delete leaf rows and validate FS/SS/FF/SF links, but its
DTO has no WBS parent, hierarchy order or summary-row contract.

This change therefore adds only `backend/app/mvp4/wbs_import.py`, a pure preview
and normalization boundary. It does not import ORM models, open a database,
modify a source, call the existing rows endpoint or authorize a commit. No model,
schema, migration, API, frontend, provider, queue or production code changed.

## Preview contract

Accepted fields are `source_row`, `wbs_code`, `client_ref`, `parent_ref`, `title`,
`kind`, `duration_days`, `dependencies` and `order`. CSV/TSV input recognizes
bounded Russian and English aliases for those fields. Every row needs either a
strict WBS code or an explicit stable client reference.

The result is an immutable preorder sequence with:

- canonical `client_ref` and canonical `parent_ref`;
- 1-based hierarchy `depth`, normalized global `order` and retained sibling order;
- explicit `summary`, `work` or `milestone` kind;
- exact duration for every leaf (`1..10000` for work, `0` for milestone);
- canonical dependencies `{predecessor_ref, link_type, lag_days}`;
- counts and maximum depth;
- fixed `requires_confirmation=true`, `commit_allowed=false` and
  `originals_changed=false`.

Hierarchy may be declared by dot-separated WBS code or explicit parent reference.
Forward references are accepted only when they resolve within the same complete
preview. A row with children is a summary; declaring it as work/milestone is an
error. Summary rows cannot carry duration or dependencies, and cannot be a
dependency target. Sibling order is deterministic: explicit order, source row,
then client reference.

All structure errors reject the complete preview with a stable content-free code:
duplicate/missing identity, missing/conflicting parent, parent/dependency cycle,
self/duplicate/missing dependency, malformed link/lag, depth over five, invalid
kind/duration/order, or row/size limits. Nothing is partially accepted or written.

## Interface request to the integrator

Do not connect this preview directly to the current rows endpoint until the WBS
persistence stream supplies one authoritative hierarchy contract.

1. Add WBS parent, sibling order and summary representation in the sequential
   current Alembic lineage; preserve all legacy rows as deterministic roots.
2. Lock project, membership, baseline and the complete graph before resolving
   preview references. Recheck draft state and graph revision with CAS.
3. Allocate real IDs only inside that transaction; map `client_ref` and
   `parent_ref`, then map dependency refs. Never persist client refs as durable
   provider identities.
4. Run hierarchy validation and the existing calendar planner over leaves before
   mutation. Summary dates/progress must be calculated from descendants and must
   not be client-supplied. Planner and financial links must reject summary rows.
5. Recheck authorization immediately before commit, append a count-only audit and
   return the authoritative saved graph. Preview itself never grants commit.
6. Keep original source/version immutable and pin source evidence separately.
   Never put source content, excerpts, paths, locators or credentials in job
   payload/audit/error data.
7. Add PostgreSQL concurrent-CAS, migration, rollback, frontend tree editing and
   real synthetic file acceptance before claiming the user workflow complete.

The dependency string grammar is compact and can be ambiguous for a custom
client reference that itself ends in `FS`, `SS`, `FF` or `SF`. Integrators should
use the structured dependency list for custom references; derived `wbs_*`
references are unambiguous.

## Verification

Synthetic fixtures only; no client documents or external services were used.

```powershell
& D:/PU-Workspace/.venv-pu-workspace-tests/Scripts/python.exe -X utf8 -m pytest `
  tests/test_v7_wbs_import.py -q --tb=short `
  --basetemp=D:/PU-Workspace/tmp/wbs-import-target-2
```

Result: **44 passed, 0 skipped**. Covered hierarchy/order inference, forward and
explicit parents, summaries/leaves/milestones, all four link kinds and signed
lags, CSV discovery, immutable output, missing/conflicting/cyclic parents,
depth > 5, invalid durations/kinds/identity, malformed/missing/duplicate/self and
cyclic dependencies, summary restrictions and hard row limits.

PostgreSQL, browser, API integration and production were not run because this is
an intentionally pure pre-integration boundary.
