# MVP2 AI Secretary — offline completion result

Date: 2026-09-04

Base: `a19fffde54e51aee0b42220c83f6c19b1d3b9055`

Branch: `codex/mvp2-completion`

## Scope and safety

This change validates the synthetic/offline MVP2 path. No Google, Gmail,
Telegram, Calendar, Tasks, or external AI endpoint was called. No credentials,
production data, raw message content, or attachment bytes were added to a job
payload or log. Existing mailbox rollout flags and live provider gates remain
default-off. No queue, model, or migration was created.

## Audit: existing → defect → result

| Area | Existing capability | Defect found | Result |
|---|---|---|---|
| Gmail routing | organization/project/contract/contact evidence, mailbox-scoped identity and dedup | low-confidence routing still materialized tasks, risks and drafts against the fallback project | all automation is deferred until exact human context confirmation |
| Contact/company | normalized organization-wide email identity, reviewable discovery, company display hint | acceptance did not prove the full confirmed-mail path or replay | synthetic regression proves one unconfirmed contact/company hint and no duplicate on replay |
| Extraction | local task, response and risk extractors | confirmation did not own a retryable deferred-analysis checkpoint | `analysis_required` is held until one idempotent materialization completes |
| Draft review | editable subject/body and explicit approval status | editing an approved draft preserved approval; recipient could not be safely edited | subject, body and one canonical recipient are editable; any later edit revokes approval |
| CONFIRM | review endpoint and manager-only external send | an editor could mark a draft approved | approval now requires project manager; rejection/edit remain editor operations |
| Reply/task status | message status and completion suggestions | UI could not distinguish “requires action” from “awaiting reply” | API returns deterministic `workflow_state` and safe reason; UI renders it |
| Outgoing completion | human-reviewed completion suggestion | replay before final checkpoint could attempt a duplicate insert | explicit message/task idempotency check added |
| Attachments | encrypted staging, opaque durable job and mailbox re-authorization | no new defect in this wave | existing contract retained; payload is only `staging_id` |

## Completed offline vertical slice

1. A synthetic incoming email is routed by exact project/contract evidence or
   held for human confirmation.
2. Unknown/ambiguous context creates no task, calendar proposal, risk, or draft.
3. Human confirmation fixes the project/contract and triggers local extraction.
4. Task/calendar candidates remain proposals; no external effect occurs.
5. A response draft exposes editable subject, body and recipient.
6. A manager confirms the exact current draft; a later edit returns it to draft.
7. Outgoing mail analysis can propose task completion but cannot complete a task
   without a separate human decision.
8. Status is derived as `needs_context_confirmation`, `requires_action`,
   `awaiting_reply`, `completed`, or `ready` without guessing provider outcome.
9. Replays do not duplicate messages, contacts, tasks, drafts, risks or completion
   suggestions under the tested synthetic path.

## Verification

Commands were executed from their respective `backend`/`frontend` directories.

```text
python -m pytest -q --basetemp .pytest-mvp2-full
1151 passed, 19 skipped

pnpm test
100 passed

pnpm check
PASS

pnpm build
PASS

alembic heads
a54f001c0a09 (head)

git diff --check
PASS
```

The 19 backend skips are existing environment-gated PostgreSQL/symlink/live
checks. The complete suite did not replace those checks with mocks.

## Live and integration gaps

This branch is **OFFLINE PASS / LIVE CONDITIONAL**. The following items require a
separate isolated environment and explicit live-test credentials:

- verify Google OIDC mailbox identity and current credential generation against
  a dedicated test mailbox;
- run Gmail ingress pagination/history behavior and provider webhooks/polling;
- import a benign test attachment through encrypted staging with API/worker
  restarts and PostgreSQL lease recovery;
- connect Gmail send, Google Tasks and Calendar to the durable provider-action
  outbox. The current legacy routes still call those providers synchronously;
- exercise timeout-before-effect, timeout-after-effect/UNKNOWN,
  reconciliation, revocation, and exact external receipts with live sandbox
  adapters;
- browser E2E of editing and confirming a draft with a real authenticated role;
- decide the durable multi-project company entity. MVP2 currently stores a
  reviewable company label on an organization-wide contact, not a separate
  company record.

Until those items pass, no claim is made that live email delivery, Tasks,
Calendar, or external AI is production-ready.
