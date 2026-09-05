# M305 meeting origin evidence — safety denial, not implementation complete

Date: 2026-09-05. Branch: `codex/mvp3-meeting-origin-evidence`.
Base: `aebd4e687d646961c9608f1a5906fba300b94aed`.

## Reproduced defect

A completed `Meeting` in project A accepted a task, obligation or decision
candidate whose current Evidence belonged to a message attachment in the same
project. The candidate's Evidence was valid, but there was no persisted relation
between that source and the claimed meeting protocol. The original regression
expected `invalid_meeting_source`; it failed because the proposal was created.

Existing meeting proposals could also be confirmed after `Meeting.minutes` was
replaced, because proposal confirmation validated candidate Evidence only. Listing
the origin likewise did not establish that the protocol remained the version
from which the proposal was extracted. A completed status and matching project
cannot establish source identity or an immutable protocol revision.

## Why existing fields cannot close M305

`Meeting` stores mutable `minutes`, `status` and `updated_at`. It has no
`SourceReference`, `SourceVersion`, document-version binding, evidence pin or
record-version CAS field. `ManagementProposalOrigin` stores the meeting ID and
candidate Evidence pins, but no authoritative meeting-protocol revision. Its
append-only nature preserves the attribution that was written; it does not prove
that attribution was correct.

Deriving a source identity from a title, namespace convention, text match, minutes
hash, source locator or timestamp would invent a binding not established by an
authorized workflow. No such fallback was implemented. **M305 remains NOT
COMPLETE.** This change closes the reproduced public source-bypass with explicit
denials; it does not implement evidence-backed meeting extraction.

## Implemented safety behavior

- New meeting proposals fail with `invalid_meeting_source` after existing
  project/actor/meeting availability checks, before business rows, origin links,
  task materialization or proposal audits are created.
- Confirmation of an entity carrying an existing meeting origin link fails with
  the same explicit outcome. The guard covers `MeetingProposalService.confirm`,
  management v2 obligation/decision confirmation, new internal-task mapping, and
  legacy management/governance promotions that would bypass confirmation.
- Legacy confirmed business records retain their statuses/history and can
  continue their existing workflow. Existing task mappings can be read again;
  this does not authorize a new materialization. Rejected/dismissed records do
  not become a path to promote an unbound proposal.
- Meeting CRUD and editable minutes remain supported. Saving completed minutes
  returns `proposal_state=invalid_source` and the reason
  `meeting_source_binding_required`; it never claims proposals were extracted.
- Historical meeting-origin lists remain project-scoped and readable, including
  after a meeting status change. Both the response and each historical proposal
  expose `origin_status=invalid_source`,
  `origin_reason=meeting_source_binding_required`, and
  `confirmation_available=false`. Stored business status and Evidence pins are
  historical data, not a newly validated source claim.
- Message proposals and manual non-meeting obligations/decisions retain their
  existing real source checks, confirmation, task mapping and digest behavior.
  No generic lifecycle or model mutation rules were replaced.

The narrow extra `api/governance.py` guard was explicitly coordinated with the
integration owner after finding that legacy Decision promotion bypassed the
meeting service. No models, migrations, jobs, provider/source reads or raw-content
audit fields were added. Generic internal lifecycle calls do not themselves
validate meeting attribution; future meeting integration must centralize the
authoritative binding check rather than invoke those lower-level methods directly.

## IR-M305-EXACT-MEETING-ORIGIN — OPEN, requires coordinated follow-up

The schema and API owners need a separate additive implementation with:

1. An authoritative project/tenant-scoped meeting protocol source identity and
   immutable source/document version, established by an explicit human binding
   workflow. Candidate Evidence must reference exactly that source/version or a
   specifically defined, provable child relation.
2. A meeting record version and CAS update contract. Replacing/rebinding a
   protocol must create/select a new immutable version and cannot silently reuse
   prior proposal authority. Existing proposal origins must retain the original
   protocol version alongside candidate Evidence.
3. A human binding API with existing role/scope/evidence checks, explicit handling
   of stale versions, missing sources and conflicting bindings. Legacy rows must
   remain unbound until reviewed; there is no automatic inference/backfill.
4. One coordinated forward migration and schema/runtime acceptance. Tests must
   cover another meeting's source in the same project, cross-project origins,
   protocol replacement, stale CAS, replay, and concurrency during binding and
   confirmation. Frontend controls should then expose verified/invalid origin
   status and enable confirmation only for a valid current binding.

No part of this follow-up is silently implemented by the denial or claimed as
complete. In particular, no new fields are emulated inside Evidence JSON or audit
payloads to avoid migration coordination.

## Verification

Regression first: **1 failed** (`invalid_meeting_source` was not raised for
unrelated same-project Evidence). Initial targeted implementation profile:
**52 passed**. All records are synthetic.

Existing positive proposal/confirmation/idempotency/digest tests now use the
actual confirmed message and its attachment Evidence. Their positive assertions
were retained, and a separate manual non-meeting obligation/decision control
confirms and materializes successfully. Meeting-specific tests assert the explicit
denial and retained historical read contract rather than claiming source proof.

Final targeted command, from `backend`, using the shared test virtualenv Python:

```powershell
python -X utf8 -m pytest tests/test_mvp3_meeting_origin_evidence.py tests/test_mvp3_meeting_digest.py tests/test_mvp3_digest_preferences.py tests/test_mvp3_management_acceptance.py tests/test_management_api.py tests/test_governance_engine.py -q --basetemp=.pytest-meeting-origin-final-20260905
```

Result: **57 passed**, no skips, in 10.11 seconds. `git diff --check` passed.

No full backend suite, production operations, push, merge or deployment were
performed in this branch. The parent integration worktree owns full verification.
