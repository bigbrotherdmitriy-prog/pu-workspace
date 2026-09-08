# v7 WBS backend result

Date: 2026-09-08. Base: `73178e0db3be8f4f2aadde339c17725b605e88be`.

## Result

The existing complete-graph endpoint now accepts a lossless WBS hierarchy without
introducing another scheduler. `ScheduleItem` has an optional self-parent,
deterministic sibling order and an explicit derived-summary flag. Existing rows
migrate as root leaves with order zero.

The service validates the complete prospective hierarchy under the existing
baseline/project lock: parent scope, parent type, cycles and depth (maximum five).
Summary nodes cannot own duration, constraints or graph dependencies. Calendar
planning runs only on leaves; summary dates and duration-weighted progress are
derived from descendant leaves. Responses are deterministic preorder and include
the exact WBS level.

Direct facts, budget and cash-flow links to summary nodes are denied. Parent
deletion requires its children to be deleted or re-parented in the same complete
request. Clone remaps both dependency IDs and WBS parent IDs.

The existing schedule screen now exposes an explicit `Обычный список / Дерево
WBS` mode switch under one edit lock. A validated document can be inspected with
`kind=schedule-wbs`; this preview is read-only, requires human confirmation and
does not claim commit. The standalone import normalizer and acceptance gate from
the parallel streams are included in this branch.

## Migration

`a54f001c0a21` is the single sequential child of `a54f001c0a20`. Downgrade locks
the table and refuses loss if hierarchy/order/summary intent exists. Runtime,
readiness and CI schema pins were advanced to `a21`; historical migration files
were not rewritten.

## Verification

- WBS/graph/schema target: 69 passed.
- Integrated WBS API/import target: 111 passed; complete schedule UI: 95 passed.
- Frontend TypeScript check and production build: PASS.
- Previous full backend run before pin updates: 2415 passed, 57 skipped; its 14
  deterministic failures were stale `a20` assertions and were rerun successfully.
- One performance smoke exceeded its ten-second budget during the loaded full run
  (22.48 s) and passed in the isolated retry; no timeout was increased.
- PostgreSQL execution of `a20 -> a21` remains required in isolated CI.

No production, provider, client document, push, merge or deploy was used.
