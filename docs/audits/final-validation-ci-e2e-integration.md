# CI diagnostics + browser E2E integration

Date: 2026-09-03. Status: **CONDITIONAL** pending a new GitHub runtime run.

## Candidate and scope

- Worktree: `pu-workspace-final-validation-ci-e2e`.
- Local branch: `codex/final-validation-ci-e2e`.
- Base/published candidate: `62b939db82167c51e3fd1b9959c9e904d0d3cede`.
- Diagnostics source `3a2b33a16e4b8bea70adcced8a38b52b65417526` cherry-picked as `cb183e1`.
- Browser source `2b29663046c505f2fd7dc38924bcc094150969e3` cherry-picked as `b1764f2`.
- No textual conflicts. No application logic or migration changes.
- Original worktrees were not modified. No push, merge, PR or production deploy.

The final integration commit adds the published candidate branch
`codex/parallel-validation-final` to the browser workflow push trigger and
adds a regression test covering both validation workflows and read-only
permissions. Before the change: 1 failed / 1 passed; afterwards: 2 passed.
The browser workflow retains frontend/workflow path filters; this candidate
changes both, so its push to that branch matches the trigger. Later backend-only
commits do not automatically rerun browser E2E under those filters.

## Verification in this combined worktree

| Check | Result |
|---|---|
| `python -m pytest backend/tests scripts/ci/tests scripts/ci/durable_queue -q -p no:cacheprovider` | 555 passed, 1 skipped, 72.40 s; run collected before the two new trigger tests were added |
| `python -m pytest scripts/ci/tests/test_final_candidate_triggers.py -q -p no:cacheprovider` | 2 passed after trigger fix |
| `pnpm run check` / `pnpm run check:e2e` | PASS |
| `pnpm run test` | 44 passed, 8 files |
| `pnpm run build --config e2e/vite.config.mjs` | PASS; output under ignored node_modules cache, not tracked backend/react_dist |
| `pnpm run test:e2e` | 15 Chromium tests passed; real frontend with synthetic HTTP only |
| actionlint 1.7.12 on all four workflows | PASS; local shellcheck/pyflakes integrations disabled |
| Alembic heads | Single head `f360a1b2c3d4` |
| git diff --check | PASS |

Backend used explicit `DATABASE_URL=sqlite+pysqlite:///:memory:` and
`PYTHONPATH=backend`. The skip is the existing PostgreSQL environment gate;
two existing Alembic path_separator warnings remain. No skip was added.
Offline pnpm installation initially lacked the Playwright package; subsequent
frozen-lockfile installation succeeded using the available package store.
Product dependencies/lockfile were not changed beyond the source E2E commit.

Browser evidence is local and untracked:
`frontend/node_modules/.cache/storage-picker-e2e/report/index.html`.
Browser warnings were NO_COLOR/FORCE_COLOR only. No real provider requests,
OAuth sessions, documents or production credentials were used.

## Remaining gate and next action

The previous durable run failed at its first Docker build, before PostgreSQL,
workers, fault recovery or queue backup/restore. This integration preserves
safe allowlisted failure classification; it does **not** claim the unknown
build root cause is fixed. Docker runtime was not rerun locally.

After explicit authorization for the final commit, push its exact SHA without
force to `refs/heads/codex/parallel-validation-final`. The local integration
branch name need not equal the remote target. The commit descends from the
published candidate, so no merge into main or force push is needed.
Expected runs: ordinary CI, Docker smoke, durable queue recovery, browser E2E.
Verify actual runs, artifacts and exact-project cleanup before changing status.

Mock-browser success does not prove live Google/Yandex integration or a full
browser/backend/PostgreSQL/worker chain. Existing connection-version/null-ID,
independent provider selection and Gmail identity/threading limitations remain.
Queue delivery is at-least-once; no exactly-once external effects are claimed.

See [runtime review](final-ci-runtime-review.md) and
[browser evidence scope](storage-picker-browser-e2e.md) for the inherited
protocols and detailed limitations.
