# Meeting-origin safety denial: frontend integration

Date: 2026-09-05. Base: `f7fc07cb344790375ec96b3d885fd5be2aa04e88`.
Branch: `codex/mvp3-meeting-origin-ui-fix`.

## Defect and bounded correction

The backend's explicit `invalid_source` / `confirmation_available=false` result
was discarded by the frontend proposal parser. A historical proposal still in
`needs_confirmation` therefore showed an enabled confirmation button, and the
hook attempted a POST the server would deny. Saving minutes also reduced
`proposal_state=invalid_source` to a generic saved notice.

- [Read model](../../frontend/src/modules/management/managementReadModel.ts)
  preserves optional `origin_status`, `origin_reason`, `confirmation_available`
  as camel-case fields. Supplied status/reason must be nonempty strings of at most
  128 characters; confirmation availability must be a boolean. Malformed fields
  fail parsing, including on an empty envelope. Legacy flagless message/manual
  payloads retain their previous shape and behavior.
- Envelope and row denial combine conservatively: neither a row nor an envelope
  with `true` can override the other's denial. There is no implemented positive
  origin-status contract, so any supplied unknown status/reason blocks confirmation
  rather than inventing a verified/bound state.
- [Panel](../../frontend/src/modules/management/MeetingProposalPanel.tsx) disables
  the button and attaches an accessible explanation. A shared fixed-message rule
  is rechecked by the [hook](../../frontend/src/modules/management/useManagementCenter.ts)
  before POST, including a matching currently loaded denial when a caller passes
  a stale flagless row. Unknown raw reasons are never displayed as trusted text.
- [App minutes notice](../../frontend/src/App.tsx) explicitly states that protocol
  source/version binding is required and not implemented, and that proposal
  creation/confirmation is unavailable. Unknown origin flags get a generic safe
  unavailable notice; saving minutes is still acknowledged truthfully.

This is UI integration of existing server denial, not an authorization boundary,
meeting source-binding implementation, new proposal workflow or production PASS.
Server checks remain authoritative. Backend, schema, providers and runtime are
unchanged. Generated `react_dist` outputs are not included in this commit.

## Verification

Baseline before implementation: three management test files **41 passed**.
Regression-first tests then produced **14 failed / 41 passed** for parser,
disabled controls and no-POST behavior, plus **1 failed** for the real App minutes
notice. After the bounded implementation those four files passed **56 tests**;
additional tests cover envelope-vs-row conflicts, unknown reasons, stale arguments
and generic App denials.

Final verification from `frontend`:

```text
npm.cmd test -- --configLoader runner
npm.cmd run check
npm.cmd run build
```

Full frontend: **26 files, 232 tests passed** (37.91 seconds). TypeScript check
passed. Build passed (1,653 modules, 17.11 seconds), with a non-fatal >500 kB
JavaScript chunk warning. Generated outputs were removed/restored only within this
worktree's `backend/app/react_dist`; no generated assets are committed.
`git diff --check` passed. Tests use synthetic
responses; no live services, backend suite, deployment or browser acceptance run.
The default test config bundler initially hit Windows sandbox directory access;
Vite's supported runner config loader succeeded without configuration changes.
Dependencies were reused through an ignored worktree-local `node_modules` junction.

Tests: [read model](../../frontend/src/modules/management/managementReadModel.test.ts),
[panel](../../frontend/src/modules/management/ManagementPanels.test.tsx),
[hook](../../frontend/src/modules/management/useManagementCenter.test.tsx),
[real App render](../../frontend/src/modules/management/MeetingMinutesApp.test.tsx).
