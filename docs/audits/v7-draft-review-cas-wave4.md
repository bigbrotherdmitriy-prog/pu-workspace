# v7 M2-04: exact draft review CAS

Date: 2026-09-08. Branch: `codex/v7-draft-review-cas-wave4`.
Base: `8794e1feed61ad5c8fd8c462e0d4e2059bfb7ac1`.

## Finding and bounded fix

Reproduced the actual API sequence: read A, save B, approve using the obsolete A
request. Before the fix the server approved B without checking what the manager
reviewed. Four regression tests failed: absent token, stale approval, missing-token
legacy approval, and simultaneous replacement/approval.

The existing response-draft PATCH now requires an exact review fingerprint and
refreshes the persisted row under a lock before comparing it. No second approval,
queue, ledger, model or migration was added. Canonical SHA-256 hashing reuses
`app.provider_actions.product._canonical_hash`. Existing project-role checks and
the provider action pipeline remain in place; this fingerprint is neither an
authorization credential nor a provider-send approval.

## HTTP contract for the UI integrator

- GET `/response-drafts?project_id=...` adds `review_token` (64 lowercase hex).
- PATCH `/response-drafts/{id}` adds `expected_review_token` with the same format.
- Missing token: 409 `draft_review_required`; stale token: 409
  `draft_review_changed`; malformed token: request validation 422.
- All changes, rejection and approval require the token; legacy writes fail
  closed. Sent/sending/unknown drafts cannot be edited. Corrective-follow-up
  status changes retain the existing separate CONFIRM restriction.
- Save changed content first. A PATCH combining changed content and approved
  returns 409 `draft_save_before_approval` without a partial write. An unchanged
  field echo with approved is allowed.
- A successful PATCH returns the new `review_token`, status and content. The UI
  must preserve its local draft on 409 and offer an explicit reload, not retry.
- Editor may save/reject, manager may approve, as enforced by the existing role
  policy. A valid token does not waive a current role check.
- No new GET-single endpoint: the existing project-scoped list is sufficient.

## Fingerprint and transaction semantics

Domain/version: `response-draft-review`, 1. Fields:

- Draft: id, project_id, reviewer_user_id, organizer_session_id, message_id,
  subject, body, recipient_to, status, source_file_id, source_file_name,
  source_excerpt, source_excerpt_hash, confidence, sent_external_id.
- Project: id, organization_id, record_version.
- Source Message (when present): id, organization_id, project_id, contract_id,
  mail_connection_id, provider_message_id, source_reference_id, context_version,
  origin_version, context_confirmed, source_type, source_external_id,
  source_sender, source_thread_id, content, context_evidence.

The fallback recipient derives from source_sender in the existing send pipeline;
therefore sender/thread changes also invalidate the review. Raw data used to hash
is not written to an audit, job or log. Updated timestamps are deliberately not
used as revisions. This is an exact state fingerprint, not a monotonic generation:
returning every included field to exactly the same value can produce the same
token (ABA). Existing current-authority checks still apply. No stronger immutable
review-history claim is made without a later versioned schema decision.

Lock order is Project -> source Message -> ResponseDraft. Permissions are checked
before locks and after draft refresh. A changed draft scope is rejected. Message
precedes draft because context analysis owns its Message before producing drafts.
`populate_existing` rejects stale identity-map review. Pending content/authority
edits are rejected rather than auto-flushed or discarded. Read-list queries also
refresh current values without flushing pending edits.

## Tests and reproducibility

Python 3.12, from backend, TEMP/TMP under `D:/PU-Workspace/tmp`:

```text
python -X utf8 -m pytest tests/test_v7_draft_review_cas.py tests/test_response_drafts_api.py tests/test_v54_email_compensation.py tests/test_mvp2_provider_outbox.py tests/test_mvp2_provider_e2e_acceptance.py -q --tb=short
```

- Before fix: 4 FAILED, 2.78 seconds.
- First targeted green: 20 passed.
- Expanded related suite: 57 passed, 26.60 seconds. Final rerun after pre-lock
  role tightening and sent-marker protection: 57 passed, 22.09 seconds, no skips.
- `git diff --check`: PASS.
- Real PostgreSQL concurrent-lock acceptance: NOT RUN. SQLite stale-session and
  interleaved committed edits do not prove PostgreSQL simultaneous execution.
- No full backend/frontend suite run here: integration owner runs the aggregate.

## Integration dependencies and limitations

Root owns App/Inbox review-token wiring and adapting the positive legacy call in
`test_mvp5_pilot_acceptance.py` to read a token before approve. This commit changes
only responses API, response/CAS tests and this audit. Other legacy clients need
the same additive read-token/write-token handshake; they now get 409, not a silent
compatibility bypass.

The existing Gmail send endpoint still accepts a legacy already-approved row and
creates its own exact provider envelope at send time. It does not accept this
review token. Closing that separate boundary requires an explicitly integrated
send contract; this patch does not claim full M2-04 external-send acceptance.
Provider hash/authority/reconciliation regression remains intact, without real
emails or credentials. Global-admin behavior remains the existing role policy,
not a new grant from this token.

A draft referencing a missing, archived or mismatched source/project currently
makes its scoped list fail closed with `draft_review_context_unavailable` (409),
rather than issuing a token for an unsafe context. Per-row unavailable rendering
can be a later read-model extension; the UI must not fabricate a token.

Original dirty worktree, frontend, models, migrations, shared CI and production
were not changed. No push, merge, PR, deploy or external effect.
