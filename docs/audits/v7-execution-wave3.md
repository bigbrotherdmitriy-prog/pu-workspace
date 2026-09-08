# v7 execution wave3 — integrated UI and CI capacity

Date: 2026-09-08. Branch: `codex/v7-execution-wave3`.
Base: `848caabf8727a6657f8f2a9d2330bde95036dfed`.
Decision: **CONDITIONAL**, not acceptance of all MVP1–9.

## Integrated history

- `51ae986`: graph editor, source `c6a891b2a78c67c369cbe037e348ea6e6fceaafb`.
- `b807512`: meeting source UI, source `ce63eb59042bce707fbf74983eae1b40e378d425`.
- `6f27cac`: parallel offline backend CI, source `349ef7c46d9dbf27eb69645a77262b15761992bb`.
- Integration changes: actual App wiring, project-scope/ABA suppression for finance and meetings, scoped role/member hints, exact minutes version, separate graph review/approval, browser/regression coverage.

No cherry-pick conflicts. Source streams did not edit App; root owns integration.
Six initial regression failures reproduced missing graph navigation and stale
finance/project/contract state. After the fix all five controller tests and the
navigation regression pass. A test hook initially returned the mock function,
causing hook timeouts; braces fixed the harness without changing its assertions.

Review found stale members/manager hints after project switch. Those are now
hidden until the selected project's main load completes; an actual App E2E holds
the new members response and verifies no inherited approval permission.

## Verification

- Full integrated frontend: **314 passed**, 31 files, 22.21 seconds; no skips.
- TypeScript `tsc --noEmit`: PASS.
- Scoped Vite build to `D:/PU-Workspace/tmp/v7-wave3-frontend-build`: PASS, 3.10 seconds. Existing >500 kB chunk warning remains; tracked react_dist untouched.
- Chromium actual-App synthetic meeting scenarios: **2/2 passed**, 11.6 seconds.
- Chromium actual-App synthetic graph scenarios: **3/3 passed**, 26.3 seconds.
- Full integrated scripts/ci suite: **261 passed**, 153.46 seconds; no skips.
- Alembic: sole head `a54f001c0a20`; schema constant unchanged.
- Full backend invocation finished: **2270 passed, 1 failed, 50 skipped**, 967.25 seconds. Its one failure was the old static meeting UI test, collected before the integration correction, which forbade editing completed minutes. The updated contract preserves explicit user action, cancelled-state denial and adds exact version/source guards. Its scoped rerun: **2 passed**. This is not an uninterrupted full-backend PASS; the next combined candidate must rerun the suite.
- Browser test TypeScript configuration has an existing unresolved `process` type (`@types/node`) under check:e2e. Browser runtime itself passed. Do not hide that limitation or claim check:e2e PASS.
- Standalone actionlint and Docker/PostgreSQL runtime not executed locally. No Docker CLI detected. Missing runtime evidence remains CONDITIONAL.

CI now separates offline tests from mandatory PostgreSQL acceptance. The final
gate requires all four jobs to succeed; absent/skipped/cancelled/failed upstream
jobs are not success. All 37 pinned PostgreSQL proofs, a20, deadlines and cleanup
remain. No queue, provider, OCR, financial or production implementation changed.

## Safety and limitations

Real external APIs, credentials and user documents were not used. Browser fixtures
are synthetic; no live mail or provider mutation was performed. Backend authority
remains decisive; UI permissions are only conservative hints. AUTO and external
actions were not enabled. No push, merge, PR or deploy.

Original dirty integration diff remains `090283159baf1bf90bad900ec2366c2bd89ce01b`.
Work is now on D; original C worktrees were retained during partial relocation.
Only exact current graph IDs are editable: WBS, graph row insertion/deletion,
working calendars, live-provider evidence and real process-fault acceptance are
not completed by this UI wave.

## Next bounded work

1. M1-02: real snapshot handler plus synthetic read-only provider across restart/lease recovery, not enqueue-only proof.
2. M2-04: reproduce stale draft approval and bind review to exact current draft content, reusing existing action envelope/authority.
3. M3-02: actual meeting confirmation before/after business commit fault proof, not generic probe job.

CI publication requires an explicitly chosen candidate. This report does not
authorize production deployment or turn missing PostgreSQL evidence into PASS.
