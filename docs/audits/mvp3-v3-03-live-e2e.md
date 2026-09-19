# MVP-3 V3-03 live E2E acceptance

## Status and scope

- Status: **PASS**.
- Date: 2026-09-19.
- Source branch/revision: `main` at `83a0179a87c134b3bf5e3307babc3dce385a1b83`.
- Isolated environment: `/opt/puw-mvp2-live-test` on `72.56.108.162`.
- Container image: `puw-mvp2-live-test:83a0179a87c134b3bf5e3307babc3dce385a1b83`.
- Database: isolated PostgreSQL volume/database belonging only to Compose project `puw-mvp2-live-test`.
- Alembic head: `f91c2d4e6a80`.
- Production and staging application stacks were not accessed or changed.

## Acceptance path

The final successful cycle used the real React UI, HTTP API, durable scheduler/worker and PostgreSQL. It did not call Python handlers directly.

1. Logged in through the UI as the isolated test manager.
2. Opened **AI Secretary**, submitted a unique synthetic message and confirmed its project context through the UI.
3. The application materialized task `5` and linked obligation `4`, due `2026-09-20`.
4. Opened **Obligations** and confirmed obligation `4` through the UI. This is a required safety gate: `needs_confirmation` obligations must not generate notifications.
5. Waited for the durable scheduler; no manual `/management/notifications/refresh` request was made.
6. Background job `13` (`notifications.refresh`) completed in one attempt and materialized notification `1` (`kind=deadline`) for obligation `4`.
7. Reloaded the UI, opened **Notifications**, and verified the new deadline notification was visible.
8. Opened **Tasks**, completed task `5` through the UI with a non-empty result note.
9. Opened the task history in the UI and verified the visible **Completed** entry and result note.
10. Read back the final state through the authenticated HTTP API.

## Durable/runtime evidence

| Evidence | Result |
|---|---|
| Task | `id=5`, `status=completed`, `due_date=2026-09-20`, `record_version=2` |
| Linked obligation | `id=4`, `task_id=5`, `status=confirmed`, `due_date=2026-09-20`, `record_version=2` |
| Scheduler job | `id=13`, `kind=notifications.refresh`, `status=completed`, `attempts=1`, `failed=0` |
| Notification | `id=1`, `kind=deadline`, `entity_type=obligation`, `entity_id=4`, `is_read=false` |
| Task history | `assigned -> completed`, action `completed`, result note present |
| Management history | action `updated`, `new_values.status=completed`, record version `2` |

Timeline (UTC):

- Task created: `2026-09-19 14:19:51.756916`.
- Scheduler job `13` available: `2026-09-19 14:26:13.662085`.
- Notification created: `2026-09-19 14:26:14.595644`.
- Scheduler job completed: `2026-09-19 14:26:14.649972`.
- Task completed through UI: `2026-09-19 14:26:17.171553`.

## Observations from the live run

- The initial message-to-project confidence was `0.7`; the UI correctly required explicit context confirmation before task materialization.
- A newly extracted obligation starts as `needs_confirmation`; the notification refresh correctly ignored it until a user confirmed it. This is expected fail-closed behavior, not a scheduler failure.
- The isolated Compose configuration originally did not forward the notification refresh interval variables into the scheduler container. The acceptance stand was updated (with a rollback copy) to pass `NOTIFICATION_REFRESH_ENABLED=true` and `NOTIFICATION_REFRESH_INTERVAL_SECONDS=60`. This changed only isolated test orchestration; application code was not modified.
- After the obligation confirmation, the scheduler path—not the manual refresh endpoint—created the notification.

## Evidence artifacts

The local evidence directory is:

`C:\Users\dpush\OneDrive\Документы\ChatGPT\Workspace\v3-03-live-runner\evidence\V3-03-1789827588971`

It contains screenshots for task creation, obligation confirmation, notification visibility and completed task history, plus the sanitized JSON result. No credentials, OAuth tokens, application secrets or `.env` content are included.

## Verdict

**V3-03 live E2E is PASS** on exact main revision `83a0179a87c134b3bf5e3307babc3dce385a1b83`: the complete UI/API/runtime cycle from a near-deadline task to durable scheduled notification and completed task history was exercised on isolated PostgreSQL.
