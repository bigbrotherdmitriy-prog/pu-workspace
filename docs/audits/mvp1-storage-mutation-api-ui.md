# MVP1 storage mutation API/UI

## Result

Added a project-scoped prepare/confirm/status/explicit-rollback API and a strict
preview/confirm/progress panel in the existing proposals screen. Exact binding,
source revision and provider IDs are resolved only on the server.

## Controls

- Viewer can inspect a redacted preview; manager role is required to enqueue.
- Request body contains only proposal/action IDs and CAS record version.
- One bounded `Idempotency-Key` becomes both command key and queue deduplication key.
- Job payload remains IDs-only; paths, locators, content and credentials are rejected.
- API execution requires both `PU_STORAGE_MUTATION_SYNTHETIC_API_ENABLED=true`
  and a `synthetic:` connection identity. Every live Google/Yandex connection is
  hard denied even when the flag is enabled.
- UI uses strict response allowlists, rejects scope mismatch/extra locator data,
  handles 409 with refresh guidance and displays measured queue progress.
- No runtime is installed automatically and no queue or migration was added.

## Remaining gates

- Synthetic browser E2E with a real worker and PostgreSQL.
- Live provider execution remains intentionally disabled.
- Operator reconciliation UI for `unknown` receipts remains separate work.
- OWNER must approve cohort management and eventual live activation policy.

## Verification

- Backend API/runtime/resolver/wiring: **20 passed, 2 PostgreSQL-skipped**.
- Frontend component/read model: **4 passed**.
- TypeScript check and production build: PASS.
- Build artifacts were restored and are not part of this change.
