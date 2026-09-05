# MVP2 Gmail history acceptance

Date: 2026-09-05

Base: `855a30df54ed7477c8180ad1c5ed920584f94656`

Branch: `codex/mvp2-gmail-history-acceptance`

## Scope and safety

This is a synthetic/offline acceptance slice. It does not connect to Gmail,
send mail, use OAuth credentials, read production data, or call external AI.
The existing mailbox identity, Source/Evidence, encrypted staging and
BackgroundJob implementations are reused. No queue, model or migration is
created.

## Audit and defects reproduced

| Area | Existing contract | Reproduced defect | Result |
|---|---|---|---|
| Gmail listing | bounded `max_results` request | only the first provider page was read when Gmail returned fewer rows plus `nextPageToken` | opaque pages are followed within one total budget; malformed, empty-progress and repeated cursors fail closed |
| Gmail history observation | `historyId` is an immutable SourceVersion observation key | behavior was not covered across a replay | a changed `historyId` creates a second immutable observation and advances SourceCurrent and mailbox-origin history without duplicating Message |
| Low-confidence replay | initial ingress defers analysis | replay could backfill a ResponseDraft before project/context confirmation | backfill now requires `context_confirmed`; no Task or ResponseDraft is created before human review |
| Mailbox origin | Message is keyed by `(mail_connection_id, provider_message_id)` | multi-page/new-project path was not accepted end-to-end | each page creates an exact Source, SourceCurrent and MailboxOriginCurrent under the verified mailbox identity |
| Reply/thread | provider thread is stored in the exact Source locator and Message projection | no combined acceptance with pagination/origin | same-thread incoming/outgoing messages remain distinct identities, retain the scoped thread and do not bypass context review |
| Attachment durability | opaque staging ID, BackgroundJob lease/recovery and live reauthorization already existed | no defect found in this wave | existing restart, expired lease, new worker, cancellation, payload and integrity regressions remain green |

## Executed acceptance path

1. A synthetic verified mailbox and a new project are created in an isolated
   database fixture.
2. The fake Gmail adapter returns an outgoing message on page one and an
   incoming message on page two with an opaque next-page token.
3. Synchronization requests only the remaining total budget on page two.
4. Both messages receive distinct mailbox-scoped provider identities, the same
   provider thread evidence, exact SourceCurrent and mailbox-origin bindings.
5. Ambiguous routing remains `needs_context_confirmation`; no task or response
   draft is materialized.
6. A replay with a new synthetic `historyId` appends an immutable observation,
   advances the origin binding revision and preserves the same Message identity.
7. Existing regressions then verify reply dispatch from the persisted origin
   after a project move and attachment import through opaque durable staging,
   restart recovery and an expired-lease/new-worker claim.

## Verification

From `backend`:

```text
python -m pytest -q \
  tests/test_mvp2_gmail_history_acceptance.py \
  tests/test_gmail_project_validation.py \
  tests/test_gmail_adapter.py \
  tests/test_v54_mailbox_identity.py \
  tests/test_v54_mailbox_identity_postgres.py \
  tests/test_v54_gmail_attachment_staging.py \
  tests/test_v54_gmail_a05_wiring.py

93 passed, 1 skipped
```

The skipped test requires an explicitly isolated PostgreSQL URL. The regression
was first run against the pre-fix code and failed twice: the second page was not
read, and low-confidence replay called draft creation. Both failures pass after
the minimal fix.

The complete backend suite was also executed:

```text
1349 passed, 20 skipped, 15 failed
```

All 15 failures reproduce when `tests/test_mvp4_supply_acts.py` is run alone
(`6 passed, 15 failed`) and are outside this change: the base test expects the
pre-DDS route set and its synthetic SourceCurrent/DocumentVersion fixture no
longer satisfies the integrated MVP4 evidence bridge. No Gmail, mailbox,
staging or BackgroundJob test failed. This branch does not modify MVP4 files.

`alembic heads` remains the single existing head `a54f001c0a17`; no schema
change was required. Python compilation and `git diff --check` pass.

## Security invariants retained

- `max_results` remains a total bound of at most 100 messages.
- Opaque `nextPageToken` is never persisted or logged.
- `historyId`, provider message ID, thread ID, addresses and content are absent
  from BackgroundJob payloads and audit details.
- Attachment jobs still contain only `staging_id`.
- Provider thread equality does not merge message identity and does not confirm
  a project by itself.
- Low confidence always requires a person before task/risk/draft extraction.
- Reply and attachment actions resolve the persisted mailbox origin rather than
  the currently selected project token.

## Remaining gates

Status: **OFFLINE PASS / LIVE AND POSTGRESQL CONDITIONAL**.

- This validates `messages.list` pagination and `historyId` observations, not a
  durable Gmail `users.history.list` checkpoint. A stored mailbox cursor,
  expiration/404 recovery and push-notification lifecycle still require a
  separate design and migration.
- RFC `Message-ID`, `In-Reply-To` and `References` are not stored. The current
  safe correlation is mailbox-scoped provider thread evidence only; it is not
  used to merge identities or auto-confirm context.
- No dedicated test Gmail account, real credential generation rotation,
  provider rate limit, token expiry or live attachment was exercised.
- The PostgreSQL mailbox isolation/concurrency test remains environment-gated.
- Restart and lease recovery use the real queue contract with synthetic
  sessions/adapters; separate API and worker OS processes were not killed in
  this worktree.
- No browser E2E was added because the change is backend synchronization and
  does not alter the existing inbox UI contract.
