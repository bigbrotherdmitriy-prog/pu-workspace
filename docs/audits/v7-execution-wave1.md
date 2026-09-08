# v7 execution wave 1 — parallel implementation and integration

Date: 2026-09-08. Branch: `codex/v7-execution-wave1`.
Base: `5226782908dff519a2bf2c6b9d320b59f2972c10`.

## Scope and result boundaries

The user requested parallel implementation of the v7 acceptance plan. Three
agents worked in separate worktrees; the integrator handled shared authority,
review, cherry-picks and CI. This wave improves concrete MVP1/2/4/7 paths and
shared safeguards. It does not declare MVP1–9 accepted or activate production.

Original integration tracked diff is preserved exactly (before/after hash
`090283159baf1bf90bad900ec2366c2bd89ce01b`). Existing meeting UI, source worktree
WIP and the original dirty main checkout were not overwritten. The old CI a19
updates were reviewed and applied independently to this clean-base worktree.
No push, merge, PR, deploy, production credential or live provider operation.

## Implemented packages

| Acceptance scope | Change | Source commit(s) | Integration commit(s) |
| --- | --- | --- | --- |
| W0 / M3-05 / shared Evidence authority | Scope-aware pending revocation guard; old/current/unknown scope; assignee lookup cannot flush pending grant | `14406dbfec6d5e280edb8d84b7f49ff86b0b0a39` | `83e6062` |
| M4-03 | Schedule import date/range/title/progress validation before writes; valid blanks and replay preserved | `acead22a40c8926f4f6b8b68cd2c30f8e6944c5d` | `372caa9` |
| M2-02 | Real local analysis engines share outer transaction; context/effects/links/checkpoint atomic; ordered Message locks | `70ca2b18cba1da731c8526868d5b595489b47922` | `8dc8aa7` |
| M4-02 groundwork | Pure FS/SS/FF/SF/lag/DAG/date/critical/float planner; infeasible fixed constraints rejected | `0233037f7a75fa576b40394bafc8f2b821218235` | `1c78e7e` |
| MVP1 format / M5-06 / M6 Evidence groundwork | Encrypted exact XLSX cell evidence and separately authorized retained child reads | `521a2d75762db42a0978da3e891f2133c1586124` | `4afafac` |
| XLSX composition | Explicit validated A05 configure links same lifecycle/storage/KEK/budget; custom ports preserved | `279d7b7fb0dc932425b0c6e37e9f26d83de49c6a` | `0aedfc9` |
| Retention/recovery | Original-only scan, Project-first lock order, bounded scoped child scan with advisory keyset cursor | `cd1826697244ca52be11c54c591f57efa6e31294` | `fe877cf` |
| M7-02 | One monthly Task/Draft period, compatible legacy lookup, locked scheduler pause check, pending edits preserved | `d67d1559ba782cc21d036ee8cd4b5548ed8828af`, `54314c1a77f7d6a91d052239beca6666b88ae844` | `fc3a80b`, `e5dc249` |

Cherry-picks had no conflicts. Shared authority and XLSX were specifically tested
together: the former broad guard had broken the derived-child publishing path.
Independent reviews found and corrected assignee autoflush, missing XLSX
composition, retention prefix starvation and conflicting lock order. None was
papered over by removing a negative test or enabling an unsafe provider primitive.

## Verification

Source-worktree results (different commits; do not sum them as unique tests):

- Authority: baseline existing regression 1 FAIL; new 8 FAIL/9 PASS, then review
  5 additional failures reproduced; final **87 PASS**, no skip.
- Schedule import: RED **31 FAIL/19 PASS**; GREEN **82 PASS**, no skip.
- Context atomicity: **79 PASS, 3 PostgreSQL skips**; independent review
  additionally ran 14 local tests. Historical orphan repair was not performed.
- Graph core: **218 PASS**, including 193 new cases and 88 independently
  enumerated date/float oracle combinations. This is not UI/DB planner acceptance.
- XLSX first scoped run: **257 PASS, 1 PostgreSQL skip**; composition follow-up
  **115 PASS, 2 PostgreSQL skips**.
- Retention: **92 PASS, 3 skips**; final added controls **9 PASS, 1 PG skip**.
- Period/pause: **40 PASS, 1 PostgreSQL skip**.

Integrated checks before the final complete run:

- Authority + import + context + neighboring regressions: **193 PASS, 3 PG skips**.
- XLSX + authority + retention + fragment reader: **202 PASS, 1 PG skip**.
- Initial complete CI contract set: **222 PASS**; subsequently expanded with
  period/retention gates and independent exact meeting node pins.
- Alembic: one head **a54f001c0a19**, matching `CURRENT_SCHEMA_REVISION`. No migration
  was introduced or rewritten. CLI heads is not PostgreSQL upgrade evidence.
- Diff whitespace and CI Python AST checks passed.

Final CI/harness set: **227 PASS** in 116.26 seconds. The first expanded run
exposed four old assertions assuming one local-upload proof; they were updated
to require exactly two and to independently reject both under- and over-counts.
No runtime gate was relaxed.

Final complete backend: **2207 PASS, 46 SKIPPED, 35 warnings** in 592.09 seconds
using Python 3.12.14, `pytest -q --tb=short`. Warnings concern Alembic's legacy
`prepend_sys_path` separator configuration. Skipped cases are not acceptance
evidence. Decision: **CONDITIONAL**, pending the real isolated runtime gates below.

## Mandatory PostgreSQL CI, not an inferred runtime PASS

Existing `v54-pilot-runtime.yml` remains isolated with read-only repository
permissions, ephemeral test secrets and owned-database cleanup. Its branch list
now includes `codex/v7-execution-wave1`. Docker smoke/readiness and durable queue
expect a19. Gzip build context and safe diagnostics are preserved.

New mandatory proofs:

1. `postgres_mvp2_context`: exact three duplicate-single/bulk-vs-single/failure-waiter
   nodes in `backend/tests/test_v7_context_confirm_postgres.py`; owned
   `puw_mvp2_test_context`, env `PUW_MVP2_TEST_DATABASE_URL`.
2. `postgres_v7_automation_period`: exact two-manual-days contention node in
   `backend/tests/test_v7_automation_period_postgres.py`; owned
   `puw_v7_test_automation_period`, env `PUW_V7_AUTOMATION_DATABASE_URL`.
3. Existing local-upload phase additionally requires Project-before-Materialization
   contention from `backend/tests/test_v7_xlsx_retention_recovery.py`, using
   `PUW_V54_LOCAL_UPLOAD_DATABASE_URL` and its existing owned database.
4. Meeting binding phase pins all four contention variants plus append-only DB
   enforcement, not just a test filename that could silently lose cases.

New env inputs are overwritten with owned values for PG and cleared before the
offline full backend phase. Mandatory skip/missing/wrong counts cannot yield
PASS; absent phases appear as NOT_RUN. Five missing-wiring regressions reproduced
before integration. Raw stderr, payloads and DSNs are not published.

Local Docker is not on PATH and not at its standard installation path. Test
PostgreSQL DSNs are not configured. Real PG concurrency/upgrade/process-kill,
Docker runtime and GitHub Actions were **not executed** in this wave. No old
remote run or synthetic result is attributed to the new candidate.

The monthly concurrency fixture uses a private UUID schema created from ORM
metadata, while the runner separately upgrades and checks the owned database's
public schema. It proves concurrency, not equivalence of all migration triggers.
Context/retention fixtures use their own migrated schemas. This distinction is
not hidden by the common PostgreSQL phase label.

## Important limitations and next steps

- **Startup remains NOT_ENABLED for staged local upload.** Explicit configure
  now correctly composes XLSX, but no production boot wiring, key or authority
  grant was invented. API-function → handler tests are not authenticated HTTP/live
  deployment proof.
- XLSX limits: 512 fragments, 256 KiB each, 4 MiB batch; formula cache freshness
  remains unverified, formulas are not recalculated. No external AI transmission.
- Meeting's default retained-original rule remains unchanged. Independently
  retained child → meeting binding and pending meeting UI still need integration.
- Retention cursor is advisory per-process traversal, not durable progress or
  authority. Restart repeats a prefix; fairness under perpetual restarts is not
  established. Every deletion still checks current capability/lineage/CAS.
- Context failure now rolls back human context confirmation too; a retry can
  complete the full transaction. Existing orphan rows need separate audited
  reconciliation. Context edit CAS and OS process-kill remain separate gates.
- Monthly canonicalization requires homogeneous upgraded writers; historical
  duplicate runs are not deleted/reassigned. Template versioning, weekly/event
  rules and Information Center are not implemented by this patch.
- Planner is pure core only: actual whole-graph CAS/schema/API/UI wiring, clone
  ID remapping, WBS and working calendars remain explicit interface requests.
  ALAP is rejected, not approximated; previous computed starts must not be reused
  as permanent scheduling floors.
- No frontend changes were made in this wave; frontend build/E2E and production
  rollout are not claimed. Original dirty meeting UI remains separate.

Next: execute isolated CI on this candidate, then finish the source→meeting UI
vertical and planner API/schema integration against the resulting clean base.
Keep full v7 acceptance matrix statuses separate from this completed code wave.

To transfer this wave onto an otherwise compatible clean checkout at the exact
base, use the ordered commit range; do not also replay the source-worktree SHAs:

```powershell
git cherry-pick 5226782908dff519a2bf2c6b9d320b59f2972c10..codex/v7-execution-wave1
```

Do not run that command against the original dirty checkout. The integration
branch already contains those changes and needs no second cherry-pick.

Future commands only (not executed; publication requires the selected candidate
to be approved):

```powershell
git push -u origin codex/v7-execution-wave1
gh workflow run v54-pilot-runtime.yml --ref codex/v7-execution-wave1 --repo bigbrotherdmitriy-prog/pu-workspace
```
