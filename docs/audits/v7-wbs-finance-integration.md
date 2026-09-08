# V7 WBS and finance integration

Date: 2026-09-08

## Outcome

The candidate integrates the hierarchical WBS editor, immutable schedule graph,
confirmed-only financial rollups, cash-gap forecast and isolated PostgreSQL
runtime/canary gates. The existing flat schedule editor remains available.

This is a local integration PASS and a runtime CONDITIONAL result. It must not be
called production-ready until the owned PostgreSQL workflows and the browser
acceptance workflow pass for the exact final SHA.

## User-visible chain

1. Select a project and its exact storage folder.
2. Analyse the immutable source snapshot.
3. Confirm the contract and its evidence.
4. Create or import WBS summary and leaf activities.
5. Link budget and cash-flow rows only to WBS leaves.
6. Review confirmed plan, commitment, actual, forecast and cash-gap values.
7. Resolve explicit OWNER/LEGAL decisions; the view never creates a payment,
   posting or automatic currency conversion.

## Integrated commits

- `027880f` — WBS storage, invariants, rollups and migration `a54f001c0a21`.
- `5caf5cc` — fail-closed WBS import preview.
- `e72f4f5` — executable WBS acceptance corpus.
- `693486a` — isolated WBS tree editor.
- `06bf2cc` — WBS API/UI integration and document preview.
- `4c218a4` — complete local WBS verification record.
- `8737fe4` — owned-PostgreSQL WBS runtime gate.
- `2407d7a` — strict financial forecast panel.
- `6a3b166` — scoped confirmed-only forecast backend.
- `28ebe5f` — workspace finance-panel integration.
- `00ffcc3` — synthetic daily-work canary gate.

## Local evidence

- Backend: `2484 passed, 64 skipped`; skips are environment/runtime dependent.
- Frontend: `403 passed`.
- Chromium browser E2E: `43 passed`, including the real App route.
- Target WBS/finance backend: `31 passed, 7 PostgreSQL skipped`.
- Target WBS/finance frontend: `109 passed`.
- Canary and WBS CI contract tests: `16 passed`.
- TypeScript check: PASS.
- Frontend production build: PASS; the existing large-chunk warning remains.
- Alembic: one head, `a54f001c0a21`.
- Evidence-backed MVP matrix: limited daily pilot `60.2%` (CONDITIONAL),
  complete MVP1-MVP7 specification `35.8%` (NOT_READY). These figures measure
  different gates and must not be combined.

## Remaining activation gates

1. Push the exact final integration SHA to a non-production branch.
2. Run the owned PostgreSQL WBS runtime workflow.
3. Run the synthetic daily-work canary workflow.
4. Run browser acceptance for the exact SHA.
5. Use one non-production pilot project with synthetic/non-client documents.
6. Only after backup and rollback verification, schedule a separately authorised
   production deployment.

No production deployment, external provider call or user-data mutation was
performed by this integration.
