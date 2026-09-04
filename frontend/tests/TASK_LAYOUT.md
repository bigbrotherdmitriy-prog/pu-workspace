# Task register layout regression

Base: `b3e2078f056bf34487220536108e28c667e7a9ad`.
Branch: `codex/task-layout-fix`, isolated worktree.

The original grid allocated fixed metadata and unconstrained action columns,
leaving the text track squeezed. Long select options and fixed history columns
also overflowed. The regression browser check failed on the original CSS at
390 px (`page overflow`). The fix puts controls below content, bounds selects,
wraps paths and makes history/completion responsive without changing handlers,
filtering, confidence or completion approval.

Run from `frontend`:

```powershell
pnpm install --offline --frozen-lockfile
pnpm run check
pnpm run test
pnpm run build --outDir dist-task-layout-check
$env:PLAYWRIGHT_CHANNEL = 'msedge'
node tests/tasks-layout.browser.mjs
```

Browser test needs Playwright available to Node and installed Edge, Chrome
(`PLAYWRIGHT_CHANNEL=chrome`) or bundled Chromium (omit channel). It is optional
tooling, not a new product dependency. The runner starts/stops its own loopback
Vite server and headless browser, blocks non-local HTTP, uses synthetic props
and all five production global stylesheets in their actual import order.
It does not mount App, use authentication or call APIs. Screenshots go to a new
`pu-task-layout-*` folder in the OS temporary directory. The fixture is not an
entry of the production build. No secrets or real project data are required.

Validation on 2026-09-03:

- TypeScript check: PASS.
- Vitest: 37 tests PASS across 10 files, including 10 new task tests.
- Production build: PASS, output redirected away from `backend/app/react_dist`.
- Headless Edge: 390, 768, 1024, 1440 px, closed and expanded states (8 checks).
  No page/element overflow or intersecting panels/controls; content occupies
  at least 80% of card width. Long Russian title, unbroken path, long assignee,
  selected evidence filename, history and completion note are included.
- Screenshots reviewed at all four widths.

Limits: synthetic component/shell validation, not authenticated production E2E;
Firefox/WebKit and physical mobile devices not tested. Native select displays
the fitting part of a long option while its full value remains available in the
option list; source/history text is not truncated. Generated build output is
disposable and excluded from the commit. No backend/quality logic, real tasks,
integrations, merge or deployment changed.
