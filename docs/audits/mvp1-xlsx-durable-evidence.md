# D14 local XLSX durable evidence bridge — work in progress

Date: 2026-09-05. Base: `2848bba170025fbc84d3d3d7bff35d1e19e13739`.

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

## Required bridge boundaries (not yet implemented)

1. Validate the entire extraction, exact original SHA-256, live source authority
   and current worker claim before creating any child rows.
2. Persist deterministic child Evidence and WRITING materializations/fences
   before encrypted I/O; incomplete evidence remains unavailable.
3. Publish the complete admitted child batch atomically; crash replay must reuse
   its durable fences. Only then invoke legacy document/business helpers, which
   currently commit independently.
4. Recover children through the existing parent job and explicit retention
   authority across admitted/writing/sealed/derived/expired states, including
   failed/dead-letter/completed parents. No second queue or registry.
5. Add a strict local-source fragment lineage branch without relaxing mailbox
   authorization or representing formula caches as verified recalculation.
