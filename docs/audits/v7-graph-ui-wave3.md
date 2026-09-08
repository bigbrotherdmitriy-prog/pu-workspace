# V7 persisted GPR editor — wave 3

Date: 2026-09-08. Base: `848caabf8727a6657f8f2a9d2330bde95036dfed`.
Branch: `codex/v7-graph-ui-wave3`. Separate worktree; original dirty worktrees preserved.
Final worktree: `D:/PU-Workspace/worktrees/pu-workspace-v7-graph-ui-wave3`.
The earlier C: copy is a backup and was not changed during the resumed work.

## Scope and reuse

Inspected `execution_finance.py` graph/status routes and a20 graph DTO, the pure
`schedule_planner.py`, existing `FinanceOperations`, and `api/client.ts`.
No AGENTS.md found in the task worktree or its workspace parent.
Only an independent frontend schedule module and this report are changed.
No backend, shared API client, App, models, migrations, production or real documents.

`ScheduleGraphEditor` uses the injected existing API function, not fetch or another
client. GET and PUT `/execution/baselines/{id}/graph` use the actual full graph DTO.
Only accepted intent fields enter PUT; server `version`, title, calculated dates and
plan never become mutation fields. The actual `graph_revision`, not display version,
is sent as `expected_graph_revision`. Dates are never calculated in the browser.

The editor exposes explicit project anchor, calendar duration, milestone, dependency
syntax (FS/SS/FF/SF plus lag), constraint and its date, and a separate not-before date.
Legacy intent stays blank until explicitly entered: no inferred duration, today
anchor, or reuse of calculated planned_start as an input floor.
Whole-graph validation/cycle/infeasibility remains the authoritative server's job.

## Concurrency, access and approval

- Internal session key binds project and baseline. Unmount invalidates request
  sequence; late GET/PUT cannot update a new selection or call its saved callback.
- Synchronous request fence prevents double submit. Buttons/fieldset expose busy.
- 409 keeps local draft and disables writes. Reload obtains a separate comparison
  snapshot, never wipes draft or rebases automatically. Explicit human choice can
  keep intent over the new revision only with the exact same item ID set and a
  draft server status. Saving is a separate subsequent action.
- Changing item membership blocks rebase. Explicit discard is labelled as such.
- Unknown network/PUT outcome also requires reload; no automatic mutation retry.
- 422 keeps editable inputs, with safe generic guidance, not raw provider/error text.
- Revoked access (401/403/404) hides local content until authorized refresh.
- All non-draft/unknown statuses are read-only; canEdit=false prevents editor writes.
  Server remains responsible for actual RBAC, scope and CAS.
- Optional canApprove is false by default. A manager reviews then explicitly
  confirms a clean saved calendar graph. The real PATCH status route receives
  `status=approved`, `expected_status=draft`, and exact loaded graph revision.
  There is no approve of dirty/legacy/unknown/conflicted state. PATCH success is
  followed by authoritative GET; uncertain approval requires reconciliation.

## Root integration contract (not performed in this fork)

```tsx
import { ScheduleGraphEditor } from "./modules/schedule/ScheduleGraphEditor";

<ScheduleGraphEditor
  projectId={project.id}
  baselineId={selectedGraphBaselineId}
  api={api}
  canEdit={canEditSchedule}
  canApprove={canManageSchedule}
  onSaved={() => { void reloadFinance(); }}
/>
```

Use existing editor/manager permissions, not hardcoded true. Reset baseline selection
when switching projects. Finance baseline list needs an open-graph action; its old
approve action should open this review instead of sending a status PATCH without
graph revision. Keep the existing create/import/clone/fact register — graph API
edits existing stages only and cannot add/delete stages or edit stage titles.
onSaved may refresh overview without forcibly closing the graph editor.

## Verification

TypeScript check passed in the initial C: run. Targeted Vitest initially could not
start under sandbox because esbuild could not read config, followed by disk-full
pause. Root relocated this isolated worktree and dependency runtime to D:.

Final commands, with TEMP/TMP `D:/PU-Workspace/tmp`, from this worktree's frontend:

```powershell
npm.cmd run check
npm.cmd test -- src/modules/schedule
npm.cmd run build -- --outDir D:/PU-Workspace/tmp/v7-graph-ui-wave3-build
git diff --check
```

- TypeScript `tsc --noEmit`: PASS.
- Targeted Vitest: **47 passed**, 2 files; 27 read-model and 20 component tests.
  Duration 2.51s. Includes invalid DTO/dates/intent, exact payload/CAS, conflict
  rebase/discard, scope races, revoked access, unknown result, approval and empty UI.
- Scoped production build: PASS, 1653 modules, 7.16s. Existing large-chunk warning
  remains (552.95 kB JS). No tracked `backend/app/react_dist` was overwritten.
- Diff whitespace check: PASS.
- Semantic review found approval review could remain open across another save;
  saving now closes it, and a regression requires fresh review of the new revision.

This fork's component is not imported into App yet. TypeScript checks it and its
component tests render it; the application build is not proof of integrated routing.

Fixtures are synthetic; all network operations use injected mock API. No live
provider, PostgreSQL or browser E2E claim. Component is not wired into App in this
fork, so full user acceptance depends on root wiring and runtime verification.
Dependencies reuse the root-prepared frozen-lock runtime through a junction to
`D:/PU-Workspace/dependencies/v7-locked/node_modules`. This agent installed none.

## Limits

- Table editor, not a drag-and-drop Gantt chart, WBS editor, working calendar or
  resource optimizer. No second planner or local forecast.
- Calculated dates are the last saved server result, clearly marked while editing.
- Legacy conversion must be saved before approval. Unknown statuses fail read-only.
- Rebase is explicit whole-intent overwrite after review; no automatic merge.
- Failure messages cannot give the server's structured task_ids through the current
  shared ApiError, which reduces validation detail but does not suppress rejection.
- Parent needs correct project selection and permission inputs. Backend enforces
  authorization; UI flags are not an authorization boundary.

No push, merge, PR or production deploy performed.
