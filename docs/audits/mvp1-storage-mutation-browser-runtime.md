# MVP1 storage mutation browser/runtime acceptance

## Scope

Synthetic acceptance for prepare → confirm → measured progress → immutable
receipt → explicit rollback, plus worker crash/replay and stale project UI state.
Live Google Drive and Yandex Disk effects remain hard denied.

## Evidence

- Browser scenarios exercise confirmation, 42% measured progress, applied
  receipt, refreshed CAS version, explicit rollback, 403 denial and late response
  after project switch.
- PostgreSQL test uses the existing `BackgroundJob` queue with two distinct
  worker identities. It persists an attempt, simulates a crash after one provider
  effect, expires the lease, lets worker two claim the job, reconciles the exact
  target state and proves there is one effect and one receipt.
- CI runs on a fresh PostgreSQL service, applies Alembic migrations and uses only
  an explicitly synthetic adapter/connection plus default-off feature flag.
- No production environment, OAuth credential or real provider is accessed.

## Status and gates

Local results:

- Chromium synthetic E2E: **4 passed**, including visible UNKNOWN without retry.
- Storage mutation component tests: **4 passed**.
- Backend target tests: **10 passed, 2 PostgreSQL-skipped**.
- Frontend TypeScript and E2E TypeScript: PASS.
- Workflow YAML and `git diff --check`: PASS; `actionlint` was unavailable.
- Local Docker command was unavailable, so the PostgreSQL worker test was not run.

PostgreSQL runtime therefore remains **CONDITIONAL** until the isolated workflow
passes on GitHub or another PostgreSQL environment. Browser mock PASS is not
presented as durable-worker PASS.

Remaining gates: true simultaneous process claim contention, operator workflow
for an immutable `unknown` receipt, and any separately authorized live provider
acceptance. No live activation is included in this branch.
