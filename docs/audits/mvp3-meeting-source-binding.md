# M3-05 exact meeting source binding — bounded backend increment

Base: `2848bba170025fbc84d3d3d7bff35d1e19e13739`. Additive migration
`a54f001c0a19`, parent `a54f001c0a18`. No production migration/deployment,
provider calls, source-byte reads, raw minutes in jobs/audit, or frontend changes.
Shared helper dependencies: `f3f7a5beb85f401e224e94bea343db0f468d1604`
and corrective `2ae7d5a` (local cherry-picks `f663fd0`, `4427dcc`).

## What becomes authoritative

A manager explicitly binds one current Meeting version to an already existing
SourceReference/SourceVersion. A title, protocol text/hash, namespace, or caller
JSON does not establish source identity. `Meeting.record_version` is CAS guarded;
binding advances it, and editing minutes/status advances it again. Old bindings
remain immutable and no longer authorize confirmation after that edit.

`meeting_source_bindings` stores UUID id/command_id, tenant/project/meeting,
resulting meeting version, exact source/version and human/time. Its composite
foreign keys enforce meeting/project and exact tenant/source observation.
Source/project authorization is additionally checked by the shared typed local
source authority. Uniqueness enforces one binding per meeting version and one
command receipt per meeting/key. ORM guards and a PostgreSQL trigger reject
binding UPDATE/DELETE. Existing immutable proposal origins gain a composite FK
to their exact meeting binding. Existing NULL origins are never guessed/backfilled.

Exact command replay rechecks current authority and returns the same binding.
Changed command input or a no-longer-current binding conflicts. Rebinding does
not relabel prior proposal origins: a pre-existing proposal with the same
lifecycle evidence identity cannot silently acquire another meeting binding.

## Authority boundary

The implementation imports the shared `require_local_upload_source` helper.
It requires real DB AuthorityState/AuthorityResolver, current project membership,
the actual local-upload owner and persisted A05 original/manifest/identity chain.
It does not substitute SyntheticPolicy grants for that mandate. The integration
fixture uses the real upload lifecycle and an empty grant set with real DB
authority, not an always-allow source resolver.

Unknown external-provider ACLs remain denied. `project_acl` JSON and a connected
provider identity alone cannot grant meeting source access. The helper's default
requires a current retained DERIVED original. A PURGED original is not opted in:
that would require independently authorized retained child evidence, outside this
increment. The positive original-retained case does not prove post-worker
original-purged eligibility, general provider support, or full M3-05 completion.

Every proposal, confirmation and new internal Task checks exact binding and
current source/evidence authority. The central lifecycle also protects generic
v2 paths. Legacy unversioned promotion routes deny meeting-origin promotion
(NULL origin: 422; otherwise versioned API required: 409), while unrelated
message/manual workflows and already-confirmed legacy workflows remain intact.
Source/evidence revocation blocks historical reads; an edited protocol with
still-authorized current evidence remains readable with `invalid_source`.

## HTTP contract

- Existing Meeting list/create responses add `record_version`. PATCH Meeting
  requires `expected_version`, `minutes`, optional status (default completed).
  The edit response adds resulting `record_version`; source becomes invalid.
- GET `/management/v2/meetings/{id}/eligible-sources?project_id=N` returns
  `meeting_id`, `meeting_record_version`, `sources` and `external_actions_created:false`.
  Each item has only `source_id`, `source_version_id`, `evidence_pins`; no raw
  content, paths, locators, account names or inferred source title.
- POST `.../source-binding` accepts `project_id`, `expected_version`, UUID
  `command_id`, `source_id`, `source_version_id`. It returns `binding_id`,
  `meeting_id`, `meeting_record_version`, `source_id`, `source_version_id`,
  `origin_status:bound`, `confirmation_available:true`, `external_actions_created:false`.
- POST meeting proposals adds `meeting_source_binding_id` to the existing body.
  Missing binding returns explicit `invalid_meeting_source`, preserving the
  fail-closed legacy outcome. Message proposal DTO is unchanged.
- Bound proposal rows and meeting envelopes include all five binding fields
  above and the bound/available flags. Invalid historical rows omit binding/source
  identifiers and carry `invalid_source`, `meeting_source_binding_required`, false.
  Empty lists still retain envelope origin status. The confirm response retains
  the existing `{proposal, external_actions_created:false}` shape, with full
  binding fields on a bound proposal. Flagless message responses are unchanged.

The UI must validate the complete bound metadata, send CAS versions, expose a
human source choice, preserve explicit denial after POST, and not turn missing
source candidates into inferred authority. Frontend work is separately owned.

## Verification and remaining gates

New contract tests first failed because no binding model/service existed.
An additional red regression reproduced a stale confirmation adding a new Task
to an already-confirmed meeting proposal. That meeting-only path now requires
the current entity version before the new effect; replay of an existing Task
remains effect-free. Message/manual semantics were not changed by this fix.
Targeted tests cover the actual local chain, source selector, bind replay,
proposal replay, one internal Task/history, edited/rebound protocol, changed
command/version, source/role/mandate/assessment revocation, exact source mismatch,
generic API bypass, API wire pins and append-only rows. Existing positive message
and manual controls remain in the regression set. Head assertions in existing
backend tests only are advanced; prior migration ranges remain unchanged.

`test_mvp3_meeting_binding_postgres.py` is an opt-in real PostgreSQL gate using
`PUW_MVP3_TEST_DATABASE_URL`: allowlisted local host, owned `puw_mvp3_test_*`
database, no caller query options. Each test creates a UUID-owned schema and
runs real migrations through a19 there, seeds the actual A05/DB-authority chain,
and removes only its own schema. It observes a real PostgreSQL blocking PID for
duplicate bind/confirmation and stale edit/confirmation races. It also verifies
the database append-only trigger. URL validation tests are not PG runtime proof.

No configured PostgreSQL runtime was available in this execution; the five PG
cases remain conditional, not PASS. Full backend/CI integration is parent-owned.

Final targeted command covered meeting binding/service/API/migration/PG fixture,
existing meeting-origin/digest/preferences/management acceptance, management API,
governance engine and the corrected shared local-source helper:
**115 passed, 5 conditional PG skips** in 23.47s. A separate migration regression
set produced **16 passed, 2 pre-existing conditional skips**. `git diff --check`
passed. Neither result is a full backend or PostgreSQL concurrency PASS.
