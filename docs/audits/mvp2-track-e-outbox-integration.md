# MVP-2 Track E — durable provider outbox integration

Date: 2026-09-18. Branch: `codex/mvp2-track-e-outbox`. Base: `f10551380b85db498fd8230e366478d4ec669c75` (`origin/main`). No push, merge, or deployment was performed.

## Owner-approved decisions

1. The UI has distinct pending, executing, unknown, applied, and failed states. A stored external ID alone never earns a checkmark; an APPLIED provider observation with an external reference is required. Explicit reconciliation can remove that confirmation if a later provider lookup no longer finds the object.
2. Both public Gmail send routes remain, but queue the same immutable `gmail.message.send` action and ID-only durable job. Approval, mailbox authority, credential generation, and replay rules are shared.
3. A single task confirmation queues independent Google Tasks and Calendar effects. Each has its own receipt. An unknown result requires provider lookup; it is never blindly resent. A failed or unknown Calendar effect does not erase the Task receipt.
4. An authorized Telegram task command may queue a previously confirmed external effect. The webhook no longer calls `publish_actions` synchronously. Moving inbound-message analysis to a background job is outside this phase.

## Reuse from the unmerged 2026-09-05 audit

The earlier `docs/audits/mvp2-provider-outbox.md` and `backend/app/provider_actions/product.py` were used as a design starting point, not cherry-picked as-is. The existing `v54_provider_actions` ledger, approvals, attempts, observations, dispatch outbox, and `BackgroundJob` queue were reused; no second ledger or queue was created. The old branch lacked the current main `/mail/drafts/{id}/send` route, current mail composer state, physical migration on the current Alembic head, project OAuth credential generation, post-send provider readback, later-deletion reconciliation, and a Telegram enqueue path. Those pieces were adapted or added here.

## Verification and limits

- An isolated PostgreSQL database `puw_track_e_test` on the MVP-2 test host was migrated to head `e73c2b4a901d`. Three real-PostgreSQL tests passed: two concurrent confirmations create one action/job; revoked approval blocks dispatch; a process crash after a Calendar effect recovers by provider lookup without a second insert. Synthetic Google adapters were used; no live Gmail, Tasks, or Calendar object was created.
- Offline tests cover exact Gmail-route convergence, content-free jobs, payload and authority recheck, scoped idempotency, partial Task/Calendar receipts, UNKNOWN reconciliation, later provider deletion, and Telegram enqueue without inline provider I/O.
- Full backend regression: `1642 passed, 37 skipped, 0 failed/errors` in 281.07 seconds. CI-script tests: `138 passed` with installed Git Bash on Windows; the first run's single shell-syntax failure came from an unavailable WSL `/bin/bash`, and the same test passed unchanged with Git Bash. Frontend: TypeScript check, `195 passed` Vitest tests, and Vite production build passed.
- Live Google provider acceptance remains **NOT RUN**. In particular, Gmail Message-ID search behaviour, Tasks lookup pagination, provider scopes, and real Calendar readback need an isolated sandbox run before a production rollout.
- Reconciliation is explicit/on demand, not a periodic provider sweep. External changes are reflected after a reconciliation job, not immediately at the moment a user edits or deletes an object in Google.
- The pre-existing synchronous `publish_actions` helper remains for compatibility but is not used by the audited send/approval/Telegram mutation routes. Inbound Telegram analysis is still synchronous by the owner's decision.

This is an implementation and test report, not a production-deployment approval.
