# v5.4 evidence product API/UI audit

Date: 2026-09-04. Branch: `codex/v54-evidence-product-api-ui`.
Exact base: `b0dbf98d82f034637512c199ba107d44a8133735`.

## Delivered boundary

- Added authenticated, read-only `GET /api/v54/evidence/{id}/fragment?revision=...`.
  Tenant, project, actor, source/version state and every authorization decision
  are derived by the server. The browser supplies only the exact evidence pin.
- Added a DB-backed product resolver over the existing v5.4 authority, source,
  assessment, identity, mailbox, policy, residency and retention facts. Both the
  pinned current version and an explicitly authorized historical version can be
  projected. `valid_until` is the earliest live expiry across authority,
  assessment, source freshness and the representation descriptor.
- Kept fragment materialization behind the existing narrow `FragmentStore`
  protocol. The app accepts an injected adapter at `app.state.v54_fragment_store`;
  an absent or malformed adapter fails closed. No model or migration was invented.
- Added a strict HTTP projection with exact evidence/source/source-version pins,
  opaque connection identity, provider and namespace, current-version identity,
  page/clause/cell/message and existing locator shapes, extractor metadata,
  confidence and human assessment.
- Exposed exact evidence pins on the existing Inbox message response and mounted
  `EvidencePanel` only in the expanded Inbox detail. `App.tsx` changed by one
  import, one optional response field and one component mount.

All not-found, cross-tenant/unauthorized, revoked/purged and unavailable-store
paths return the same content-free 404 body with `Cache-Control: no-store` and
`Pragma: no-cache`. The store is not called before all gates pass. The UI does
not infer authority, maps transport/schema failures to the same unavailable
card, does not fetch while collapsed, and clears fragment state on close.

## Explicit exclusions

No evidence mutation, extraction/OCR, provider call, external AI/model call,
queue job, schema migration, production default, merge, push or deployment was
added. This flow does not claim a production materialization backend exists.

## Verification

- Targeted backend evidence/API regressions: **100 passed**.
- Targeted frontend evidence regressions: **47 passed**.
- Full frontend unit suite: **91 passed**.
- Full frontend TypeScript check: **PASS**.
- Full frontend production build: **PASS**.
- Backend bytecode compilation: **PASS**.
- Full backend suite: **977 passed, 11 skipped** (four pre-existing Alembic
  deprecation warnings).
- `git diff --check`: **PASS**. Base/branch and tracked-file hygiene were
  rechecked before the final commit.

The tests use only synthetic SQLite records and an in-memory fake fragment
store. They establish contract and regression behavior, not production storage,
provider, PostgreSQL, volume, latency or security certification.
