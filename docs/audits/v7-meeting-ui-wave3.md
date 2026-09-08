# v7 meeting source UI — isolated integration package

Date: 2026-09-08. Base: `848caabf8727a6657f8f2a9d2330bde95036dfed`.
Branch: `codex/v7-meeting-ui-wave3`.

## Scope and implementation

Reviewed and selectively reused the existing uncommitted meeting UI proposal
without changing its original worktree. Only the meetings module, its source
model/panel/tests, management proposal read model and synthetic browser scenario
are included. `App.tsx`, backend, migrations, API client and provider code are not
changed. No external call, push, deployment or production mutation was performed.

The panel uses the existing API client (also injectable for tests). Discovery and
origin history are GET requests. Binding, preparing a proposal and confirmation
are distinct explicit human operations. Binding includes the exact meeting
version and source/version IDs; proposals include the exact selected evidence
pins and binding ID; confirmation uses the exact proposal revision.

Additional semantic corrections to the reused draft:

- Preserve every positive server evidence revision, not a fabricated revision 1.
- Reject stale/cross-meeting history, malformed pins and duplicate sources/pins.
- Clear the old binding on project changes even without a parent remount.
- Historical denied proposal rows never inherit the current envelope's binding.
- Disable all confirmation controls while any mutation is pending.
- Keep the human action draft on conflict/retention denial; do not retry POST.
- Display the panel only for a completed meeting with a known positive version.
- Explain that retained evidence does not imply the original file is available.

The eligible-source backend already applies retained-child authorization. The UI
does not infer retention authorization from a filename, original state or local
flag, and does not download an original or bypass a server denial. At present the
endpoint supplies IDs/pins, not human-readable cell content: the panel displays
exact IDs and does not invent a preview or assessment.

## Root integration contract

`MeetingsModule` accepts:

```ts
projectId?: number | null;
members?: { user_id: number; name: string }[];
onMeetingVersionChange?: (meeting: MeetingRow, version: number) => void;
// MeetingRow includes record_version?: number.
```

Root must pass the current project and its member list, and update the matching
meeting's record_version after binding only if the original project/meeting scope
is still active. The protocol PATCH must send `expected_version` from the meeting
row, never a hardcoded value or an inferred increment. Existing protocol draft
handling must not clear the draft on a conflict. Add an App-level assertion of
that PATCH revision. The added Playwright scenarios depend on this wiring and
must be executed in the integrated branch; this branch intentionally does not
alter `App.tsx`.

## Verification

Run from `frontend` with TEMP/TMP on `D:/PU-Workspace/tmp`:

```text
node node_modules/vitest/vitest.mjs run src/modules/meetings/MeetingSourcePanel.test.tsx src/modules/management/managementReadModel.test.ts
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vitest/vitest.mjs run
```

- Targeted: 54 passed (22 panel/model cases plus 32 existing management cases).
- TypeScript: PASS.
- Full frontend: 258 passed, 27 files, 35.26 seconds; no skips.
- Browser E2E: NOT RUN here, requires root App wiring.
- Backend/PostgreSQL/live providers: NOT RUN; no backend change.
- Initial C: installation was interrupted by ENOSPC. Worktree/dependencies were
  relocated by the integration owner to D:. No user data was removed by this task.
- An initial Vitest invocation from repository root failed setup resolution
  before collection; subsequent commands use the proper frontend cwd.

Status: component/unit readiness only; integrated browser and live acceptance are
not claimed. No whole-MVP acceptance percentage is inferred from unit results.
