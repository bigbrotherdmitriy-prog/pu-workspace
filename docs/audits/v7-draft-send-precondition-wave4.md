# v7 explicit Send view precondition

Date: 2026-09-08. Branch: `codex/v7-draft-review-cas-wave4`.
Parent: `8811294bce01e201f4c3d6c7cbb2e3925249f2a1`.

This is a separate follow-up to the review PATCH contract. It closes the legacy
HTTP Send-view gap documented in `v7-draft-review-cas-wave4.md`, not live-provider
or PostgreSQL fault acceptance.

## Reproduced defect

Before the change, opening approved A, changing persisted content to approved B,
then sending from the old A view queued B. A legacy Send without any review token
also queued the approved row. Regression: **2 failed, 2 passed** in 1.66 seconds.
The RED helper deliberately invoked the old signature to demonstrate unsafe
enqueue rather than merely failing on an unknown Python keyword.

## Contract

`POST /response-drafts/{draft_id}/send-gmail` accepts:

```json
{"expected_review_token": "64 lowercase hexadecimal characters from the current read model"}
```

Missing body/field: 409 `draft_review_required`. Stale token: 409
`draft_review_changed`. Malformed supplied token: validation 422. The current
row must still be approved. Reading an approved row and explicitly clicking Send
with that token is allowed; no new approval is fabricated. Root owns UI wiring
and must not silently send on loading a token or automatically retry a conflict.

The endpoint reuses the review helper's Project -> Message -> ResponseDraft lock
and refreshed exact state fingerprint. Token checking precedes mailbox resolution
and `queue_confirmed_action`. Source sender/thread/context changes invalidate the
token, including the fallback recipient case. Current project rights and current
mailbox action authority remain required. The already-sent path checks the same
current view/rights before returning an existing provider ID, never re-enqueues.
Corrective follow-up retains its separate CONFIRM-only restriction.

`queue_confirmed_action` already commits internally. The sending transition is
now set before that call and commits with the existing outbox/job, not in a later
transaction that temporarily leaves a reusable approved token. Queue errors roll
back the transition. After successful enqueue the approved token cannot be used
again because current status is sending. No worker, queue, model, migration,
provider envelope or autonomous behavior was changed.

This token binds the explicit human Send request to viewed state; it is not a
persistent approval record or an authorization credential. The existing worker
still validates exact provider envelope, mailbox generation and current policy
immediately before any external effect. State-fingerprint/ABA limitations from
the parent report still apply; no exactly-once live guarantee is claimed.

## Verification

Synthetic fixtures only. Queue double for endpoint negatives; existing provider
outbox/mailbox tests retain their assertions, using a freshly read token.

```text
python -X utf8 -m pytest tests/test_v7_draft_send_precondition.py tests/test_v7_draft_review_cas.py tests/test_response_drafts_api.py tests/test_mvp2_provider_outbox.py tests/test_mvp2_provider_e2e_acceptance.py tests/test_v54_mailbox_identity.py tests/test_v54_email_compensation.py -q --tb=short
```

- Initial send-related green: **53 passed**.
- Final combined regression: **100 passed**, no skips, 8.88 seconds.
- OpenAPI request body checked; stale identity map, current role revocation,
  fallback sender changes, missing token, already-sent disclosure denial,
  sending-state atomic commit and rollback are covered.
- `git diff --check`: PASS.
- PostgreSQL concurrent sends/process faults: NOT RUN. SQLite interleaved sessions
  do not prove PostgreSQL contention semantics.
- Real Gmail/OAuth/Telegram, production, full backend and frontend: NOT RUN here.
  Integration owner owns whole-candidate and browser checks.

Files: `backend/app/api/gmail.py`, `backend/tests/test_v7_draft_send_precondition.py`,
`backend/tests/test_mvp2_provider_outbox.py`,
`backend/tests/test_v54_mailbox_identity.py`, and this report. No source credentials,
document bodies or tokens were added to job payload/audit/diagnostics. No push,
merge, PR, deploy or real email occurred.
