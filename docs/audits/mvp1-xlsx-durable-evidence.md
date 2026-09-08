# D14 local XLSX durable evidence bridge — bounded staged-upload slice

Date: 2026-09-05. Base: `2848bba170025fbc84d3d3d7bff35d1e19e13739`.

Resumed as the bounded v7 XLSX slice on 2026-09-08, from local prerequisite
HEAD `2ae7d5a` (including `f3f7a5b`). Compared against integration
`5226782908dff519a2bf2c6b9d320b59f2972c10`: the committed local-source helper,
local-upload hook target and fragment-reader target match. Integration additionally
contains the shared AuthorityResolver membership-refresh correction; it is a
required integration dependency, not modified or copied into this scoped package.
No shared authority/model/migration/CI/frontend changes are included.

## Shared prerequisite: exact local-source metadata authority

[local_source.py](../../backend/app/source_evidence/local_source.py) provides
`require_local_upload_source` for an existing A05 local-upload source and exact
revision pin. It returns a frozen `LocalSourceBinding` DTO, not plaintext or an
invented grant. A real `AuthorityResolver` checks the persisted mandate and live
membership; typed source/version refs, owner/project/tenant, deterministic A05
lineage, SHA-256 observation, policy pins, residency, retention and original
materialization bindings must agree. No MailConnection is manufactured.

The default only accepts a live DERIVED original. The explicit
`allow_purged_original` option accepts metadata of a completed PURGED original
within its original policy deadline; it does **not** promise original bytes.
Callers using that option must separately authorize an active child representation.
Meeting binding is expected to keep the default false. This prerequisite alone
does not persist XLSX cells, implement local fragment reads, or close D14.

Baseline before work: existing materialization/local-upload tests **18 passed**.
[New authority tests](../../backend/tests/test_mvp1_local_source_authority.py):
**13 passed in 2.71s**. The positive fixture uses persisted AuthorityState with
real AuthorityResolver and no fallback synthetic grants. Tests deny missing or
revoked mandate, changed membership, missing permission, expired retention,
revoked source, spoofed provider, malformed checksum, copy-enabled original and
wrong version; metadata reads never open staging bytes. SQLite does not establish
PostgreSQL lock/concurrency behavior. No production or live-provider claim.

Review regressions reproduced five incomplete `representation_ref` checks
(pin kind, source ref, version, unknown fields and expiry). Full typed descriptor
and manifest pin equality now fail closed; the wrong manifest pin-kind control
already denied and remains explicitly checked. Policy pins here establish typed
immutable chain agreement; this helper is **not** a standalone resolver of current
retention-policy rules. Authorization comes from the actual DB mandate plus the
bound local-chain facts and their explicit deadlines.
Final shared-helper profile: **36 passed in 6.21s**, including 19 helper cases and
existing materialization/local-upload lifecycle regressions.

### Corrective review: pending ORM revocation

Review found that `populate_existing` under `no_autoflush` could overwrite a
caller's pending revocation with the persisted active value. Regression-first
reproduced **7 failures / 1 passing control**. A pre-query guard now denies
relevant new, dirty or deleted authority/lineage rows before any resolver or
refresh query, without flush or rollback. It examines previous identity/scope
values too, so moving a pending membership or original materialization cannot
evade the guard. Unknown scope fails closed. Unrelated rows and new child
evidence/materializations are not mistaken for original authority.

The expanded controls preserve pending source deletion, mandate revocation,
SourceCurrent, original materialization, member/project/user changes, identity,
version, original evidence, newly added/deleted mandates and moved scope rows.
Targeted helper + A05 + lifecycle result: **51 passed in 13.64s** (SQLite).
This does not establish PostgreSQL concurrency behavior or production readiness.

## Connected implementation and transaction boundaries

[xlsx_ingestion.py](../../backend/app/source_evidence/xlsx_ingestion.py) is called
by the existing `LocalUploadBusinessProcessor` in
[local_upload_staging.py](../../backend/app/local_upload_staging.py). The explicit
`configure_local_upload_runtime` callback now binds the built-in processor to
`XlsxEvidenceIngestion` using the adapter's exact existing A05 backend;
it does not manufacture grants or create a second queue. The existing staging
job contains only `staging_id`. No provider, external AI or formula evaluation is
introduced. Legacy TSV text still feeds the existing document/import helpers.

The bridge verifies actual original bytes against the staged SHA-256, size,
exact SourceVersion revision, owner/project/tenant, A05 observation and current
worker claim. Each parser-verified sheet/cell locator creates immutable Evidence
and a child Materialization linked by their existing typed columns to that same
SourceVersion and original parent. Formula and cached value are separate
`sheet_cell` evidence entries; sheet identity and A1 address come only from the
bounded parser. There is no new table, migration or arbitrary JSON lineage.

The encrypted `v54.xlsx_cell.1` fragment contains the exact locator, original
SHA-256, observed formula/value and cache state. Missing/empty formula caches
retain their manual-review indication without an invented value. `cache_freshness`
is always `not_verified`, `formula_recalculated` is false and evidence is initially
unverified with unknown confidence. Extractor `configuration_digest` is a stable
configuration hash, **not** a payload hash. Content integrity is provided by the
existing authenticated encrypted storage envelope/footer and its exact manifest.

1. Preflight the entire bounded fragment plan and actual DB authority before
   child admission; wrong version/checksum/authority or budgets create no children.
2. Commit all child Evidence with unavailable assessments and WRITING registry
   rows/fences **before** child ciphertext I/O. Every child write rechecks the
   exact live job claim; cached job ownership/payload/cancellation is refreshed.
3. Seal every child, then publish all representation references and assessments
   in one DB transaction. A partial failure leaves only the durable unavailable
   admission batch. The encrypted files remain registered for replay/retention.
4. Replay uses the same deterministic evidence/materialization/object IDs and
   durable fences; existing complete ciphertext is reused rather than replaced.
   Complete DERIVED replay is separately revalidated. Registry cleanup removes
   only eligible partial fences while the exact job claim is locked.
5. Only after durable publication do independently committing legacy business
   helpers run. This is **not** one atomic transaction across all legacy business
   effects. Their existing deduplication boundaries remain in force; tests cover
   failure before indexing and replay without duplicate/replaced cell evidence.

## Retention and exact reads

Children keep the original owner's residency, KEK and retention deadline;
`copy_allowed=false` and explicit derivation permission are enforced. Original
cleanup can complete while authorized derived fragments remain until that
deadline. The existing `recover_local_upload_retention` hook dispatches bounded
child cleanup through the configured processor, using the actual server-owned
`LocalUploadRetentionAuthority`, not a fabricated user grant. It validates local
A05 parent/source/version/proof identity and a terminal original job, and handles
ADMITTED, WRITING, SEALED, DERIVED and EXPIRED children. Failed deletion is
repeatable; WRITING fences remain durable until eligible partial deletion succeeds.
Evidence rows remain immutable while registry tombstones prevent further reads.

Writer and child cleanup acquire project authorization before parent and child
locks, matching the reader's project-before-materialization order. Review found
and corrected both parent/child and project/child lock-order inversions; this is
code-order evidence, **not** a PostgreSQL concurrency PASS.
Each cleanup call inspects at most `4 * limit` candidates and purges at most
`limit` (1–500). Invalid bindings and failed deletions stay denied/retryable;
this bounded scan is not a guaranteed cleanup latency or an unlimited drain.

[fragment_reader.py](../../backend/app/source_evidence/fragment_reader.py) now has
an exact DB-local-upload branch using `require_local_xlsx_fragment`; other sources
still require their existing mailbox linkage. The local branch checks real DB
authority and original lineage, a live no-copy child, full manifest and descriptor
pins, parser identity, exact original digest and strict encrypted payload. Pending
child security changes deny before refresh; clean cached children are refreshed.
The final clock check denies a fragment whose deadline expires during storage I/O.

For a future retained-child consumer, call the existing `read_fragment` with the
**exact Evidence revision pin**, actual `ProductEvidenceResolver` and request-bound
`MaterializedFragmentStore`; do not authorize a source by merely setting
`allow_purged_original=True`. The local helper's explicit opt-in is only used
alongside independent exact child authorization. M305 meeting binding is unchanged:
its default DERIVED-original requirement is not silently relaxed by this slice.

## Limits, evidence and remaining gates

The bridge admits at most **512 formula/cache fragments**, **256 KiB per
fragment**, **4 MiB aggregate plaintext fragment payload** before encrypted I/O.
Existing XLSX ZIP/XML/parser limits are unchanged. A workbook exceeding any bridge
budget is denied as a whole; operator recovery requires a separately scoped smaller
upload or an explicit future budgeted paging design, not silent truncation. Empty
or identity-unverified workbooks with no exact formula/cache locators are denied.
XLS, native Google Sheets, alternate ingestion paths, source-bound meeting
consumption and production rollout are outside this slice. Formula caches are not
proof of freshness or recalculation. These metadata/policy-pin checks do not replace
a standalone current-retention-policy resolver.

[Synthetic integration tests](../../backend/tests/test_mvp1_xlsx_durable_evidence.py)
include original-version persistence, exact reads after original cleanup, no-copy
and IDs-only job/audit controls, budget/authority/version/checksum denial, partial
write and post-ciphertext process crashes, idempotent replay, expired/stale job
ownership, pending/stale child denial, all five cleanup states, retryable deletion,
running-parent cleanup exclusion, provider-spoof cleanup denial and TTL crossing
during actual encrypted reads. Regression-first reproduced missing durable cells
and mandatory-mailbox rejection before the bridge, plus stale-child, stale-job and
read-crosses-TTL failures during review.

Final resumed targeted profile: **257 passed, 1 skipped in 57.78s**. It includes 34 D14
controls, parser, shared authority, existing fragment reader, staging/A05/lifecycle,
structured import and local document tests. The skipped existing A05 PostgreSQL
lease test is conditional on an explicitly configured safe local test database;
it is not a D14 concurrency proof. SQLite synthetic results do not establish live
PostgreSQL lock/replay behavior, platform filesystem safety or production readiness.
No real provider, credential, real user file, external AI, deployment or live mail
was used. Runtime composition, owned PostgreSQL crash/concurrency acceptance and
owner retention-policy acceptance remain explicit gates.

The five resumed controls explicitly deny cross-project/tenant scope, wrong
Evidence revision, mismatched source-version representation pin and an original
purged with a non-completed outcome, before ciphertext reads. An initial test
fixture tried to persist SourceVersion revision 2 and was correctly rejected by
the existing immutable-revision DB constraint; the final test instead exercises
the meaningful forged representation-pin boundary. No schema guard was weakened.

## Corrective composition slice (2026-09-08)

Base: `521a2d75762db42a0978da3e891f2133c1586124`. Review identified that this
commit required manual XLSX injection, while the repository had no production
configuration caller. Configuring a bare `LocalUploadBusinessProcessor()` would
therefore deny XLSX jobs. Six regression-first configuration tests failed before
the correction and passed after it.

The existing explicit configure callback now validates the adapter's A05 backend,
identical storage instance, KEK and file-size budget before installing the built-in
processor. Missing XLSX ingestion is constructed from that same backend; an
existing ingestion must already reference it. Missing/wrong lifecycle, foreign
ingestion, malformed ingestion, storage, KEK and budget mismatches fail closed
without replacing the currently installed runtime or mutating the candidate's
ingestion. Repeated valid configuration reuses ingestion; `None` still disables
the runtime. Custom processor ports retain their existing contract and are not
replaced; their acceptance does not establish XLSX evidence support.

The D14 fixture now configures a bare built-in processor, without manually
constructing ingestion. A new synthetic test invokes the actual local-upload API
function with its real DB role check, verifies the IDs-only queued payload, runs
the existing job dispatcher under a real queue claim, and reads independently
authorized cells after original purge. This is same-thread API-function coverage,
not an HTTP authentication or deployment smoke test.

Scoped result: **115 passed, 2 skipped in 25.08s** across D14 (43 controls), local
source authority, local staging, A05 wiring, staging safety and conditional A05
PostgreSQL tests. The conditional PostgreSQL tests remain unexecuted; three
existing Alembic configuration deprecation warnings were emitted. No full suite,
real data/provider, credentials, deployment or production runtime was used.

**Startup wiring remains NOT_ENABLED.** No `main.py`, environment flag, default
authority, key provisioning or startup caller was added. An authorized deployment
must explicitly supply its independently configured backend and install runtime
in both API and worker processes. This correction closes the local composition
dependency, not that deployment/owner acceptance gate.
