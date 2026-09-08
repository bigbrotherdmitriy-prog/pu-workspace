# Schedule row lifecycle UI — wave 5

Date: 2026-09-08. Branch: `codex/v7-schedule-plan-ui-wave5`.
Parent commit: `4aa0f37f3b1028767b4e62e56fefce072d8ab7cf`.

## Contract and scope

Reviewed the sibling schedule-rows backend `ScheduleGraphRowsPut` and `put_schedule_graph_rows` implementation. This commit depends on that endpoint being integrated: `PUT /execution/baselines/{id}/graph/rows`. No backend change is included here.

An explicit composition editor is embedded inside `ScheduleGraphEditor`, without App.tsx changes. It supports add, rename, deletion marking, calendar intent/constraints and references to new rows (`new1FS`, `new2SS+2d`). The full request contains existing IDs or client refs, a complete deleted-ID partition and typed dependencies. Temporary local refs are not fabricated server IDs. Dangling references are rejected locally, not silently removed. Existing intent validation is reused; calculations remain server-owned.

Only explicit Save sends the atomic snapshot. Approval is a separate existing action and is disabled while composition changes remain unsaved. Composition and existing graph editors block each other's writes when dirty. Readonly/approved states cannot add or save. Server financial/source/actual-data deletion protection is not bypassed; the UI never sends unlink operations.

Responses require a valid full plan, exact next graph revision, unchanged baseline version, complete unique client-ref mapping, no reuse of previous IDs for new rows, exact returned titles/intent and canonical resolved dependencies. Malformed/unknown/error outcomes preserve local rows and block blind retry. Reload fetches comparison data only; explicit discard/accept resets the composition, including same-revision rejected operations. Automatic row rebase is disabled. In-flight writes block competing reload; remount fences late responses across project changes.

## Checks

- Tests added before implementation: RED import failure for the not-yet-implemented lifecycle model (not claimed as an existing backend defect reproduction).
- `node node_modules/vitest/vitest.mjs run src/modules/schedule`: **74 passed**, six files. Seven added lifecycle model/component tests cover exact new mapping, rename atomicity, separate approval, new-row references/deletion after 409, dangling dependency refusal, approved readonly, and late save/project change.
- Existing late-GET test selector changed from unqualified spinbutton to the existing named duration input; original stale-response assertion is retained.
- `node node_modules/typescript/bin/tsc --noEmit`: PASS.
- `git diff --check`: PASS.

## Limits

Synthetic transport tests only: no live provider, PostgreSQL, browser E2E or full backend run. Backend endpoint integration and aggregate tests remain root-owned. On unknown save outcomes, user must fetch and compare/discard before a subsequent submission; no replay is attempted. UI does not inspect financial records to predict protected deletions, and explicitly reports that the server can reject them. Raw backend errors are not rendered.

Only schedule module/tests and this report changed. Original dirty worktree, App.tsx, backend, migrations and production untouched. No push, merge or deploy.
