# V7 schedule saved-plan UI — wave 5

Date: 2026-09-08. Branch: `codex/v7-schedule-plan-ui-wave5`.
Base: `fe1bf865aa349d818921f897ffae827e66541341`.

## Audit and implementation

The existing backend graph response already exposes `plan` (`SchedulePlan` / `TaskPlan` in `backend/app/mvp4/schedule_planner.py`). The frontend previously discarded it. This package consumes that existing response; it adds no endpoint, mutation, date calculation or second planner.

`parseGraph` now retains the saved plan after validating its exact fields, IDs, complete task/topology sets, dates against saved graph items, nonnegative integer floats, critical membership, dependency membership/topology and duplicate critical edges. Missing/null legacy plan remains explicitly unavailable; malformed non-null plan fails closed. Empty plans do not invent a finish date or zero task reserves.

`SchedulePlanSummary` displays saved version/revision, server dates, critical work IDs/titles and edges, and each task's total/free float in calendar days. Critical work is not misleadingly represented as one guaranteed path: fixed-date work or multiple branches can exist. Approved graphs are read-only. Local edits are explicitly excluded from the displayed saved calculation; pending/conflicting responses hide the summary. Stale project responses remain fenced by the editor session, and older revision responses are rejected.

The summary is wired inside `ScheduleGraphEditor`; no App wiring is needed. No change to existing request bodies or approval permissions. There are no summary action callbacks or network calls. Titles render as React text, never raw HTML.

## Test evidence

Commands run from this worktree's `frontend` on synthetic fixtures:

- Before implementation: `node node_modules/vitest/vitest.mjs run src/modules/schedule/schedulePlanReadModel.test.ts` — **13 failed**, reproducing discarded plan and accepted malformed plans.
- After implementation: `node node_modules/vitest/vitest.mjs run src/modules/schedule` — **67 passed**, four files. Includes project-switch late response, old revision reload, approved read-only, missing/incomplete plan, critical identity/edge validation, exact server values, empty plan, dirty warning and escaped titles.
- `node node_modules/typescript/bin/tsc --noEmit` — **PASS** after explicit callback typing in test data.
- `node node_modules/vite/bin/vite.js build --configLoader runner --outDir D:/PU-Workspace/tmp/schedule-plan-ui-wave5-build` — **PASS**. Existing-size warning: JS chunk above 500 kB; output only in task-owned D temporary directory, no tracked generated assets.
- `git diff --check` — **PASS**.

## Boundaries and limitations

Only six files under `frontend/src/modules/schedule/` and this report are changed. App.tsx, backend, shared APIs, migrations, production and source dirty worktree are untouched. No push, merge or deploy.

This is structural validation and read-only projection, not an independent proof of planner mathematics. Tightness/completeness of critical edges and numeric date arithmetic remain the existing backend planner's responsibility; the browser does not recalculate them. A null plan cannot establish criticality. No real provider, browser E2E, PostgreSQL or full backend run was performed in this bounded package; aggregate integration remains the root flow's responsibility. No claim that all schedule acceptance criteria are closed.
