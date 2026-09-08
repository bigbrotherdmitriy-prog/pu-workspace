# v7 wave6: consistent read-only DDS views

Date: 2026-09-08. Branch: `codex/v7-execution-wave6`.
Base: `6fdd2564d56f7981d93453fdd2d910559130246f`.
Decision: **CONDITIONAL**, not completion of the complete MVP1–9 specification.

## Integrated work

| Local commit | Source | Scope |
| --- | --- | --- |
| `7be7090` | `3b3e7e61fadbfa386fe85dd0dbea6fea192e5bb6` | One-query DDS views |
| `14089ed` | `a5bf4ed3572cfa278b8d0f269d0bfda9bbd2666d` | Unconfirmed plan / zero fact exclusions |
| `b5a2576` | `e11f614fc32bb578f789011ab42027865977ac2a` | Scoped UI and decimal validation |

Separate worktrees, no cherry-pick conflicts. Root adds actual-App browser
acceptance, branch-scoped workflow triggers, explicit project-wide metric label
and documentation. Existing payment/confirmation/correction handlers unchanged.

The same CashFlowEntry query produces details, calendar, months and summary.
Plan and actual use their own dates; no double addition. Money remains exact
Decimal strings, checked in browser as integer minor units with BigInt. One
explicit project/contract/period read drives all four views. Old responses are
discarded on scope or period change, including ABA. There is no bank opening
balance, conversion, new ledger, payment or posting.

Semantic review found two defects before integration: legacy nonconfirmed rows
were counted as confirmed plan, and zero actuals were accepted as facts. Eleven
red regressions reproduced the issue; the fixing commit requires confirmed review
for plan and positive reviewed actual. Exclusions remain visible. This intentionally
does not backfill legacy confirmations or change finance handlers.

## Verification

- Integrated DDS/budget/graph/planner/backend targets: **310 passed**, no skips,
  27.51 seconds; 2 existing Alembic deprecation warnings.
- Full frontend: **368 passed**, 38 files, 18.21 seconds; TypeScript PASS.
- Actual-App synthetic Chromium: **8 passed**, 13.5 seconds (DDS, graph, draft).
  After adding the project-wide metrics warning, DDS browser repeat: **1 passed**,
  6.0 seconds. These tests do not invoke real providers or backend PostgreSQL.
- CI contracts after branch extension: **53 passed**, 0.62 seconds.
- Browser harness built the web application; existing >500 kB chunk warning remains.
- Previous base wave4 full backend: **2352 passed, 55 skipped**, 1233.54 seconds.
  This is base evidence, not a full backend run for this new candidate. Scoped
  changed-path regression passed; the candidate still needs complete CI acceptance.
- No schema/model changes: a single `a54f001c0a20` remains. Forty-four mandatory
  PG proofs are retained in the inherited runner; none executed locally here.

## Boundaries and next work

M4-05 four-view implementation is present, but whole financial acceptance is not
closed. Existing overview cash-gap ordering and forecast proposed-row basis remain
separate reported limitations. Global overview is now explicitly labelled
project-wide; it must not be confused with selected-period/contract DDS. Currency,
tax, retention and partial-payment decisions are not invented.

Runtime release blockers: real PostgreSQL concurrency, migrations, business
crash/recovery/restore and both workflow results on the same SHA. Docker CLI was
found but server availability not established. No live APIs, credentials, user
data, production, push, merge, PR, deployment or global cleanup used.

Worktrees and generated test/build outputs are on D. Original C dirty worktree is
preserved. See [remaining scope](v7-next-acceptance-checkpoint.md) and
[user instructions](../user-guide/v7-reviewed-workflows-ru.md).

Future commands, **not executed**, from this worktree after candidate approval:

```text
git push -u origin codex/v7-execution-wave6
gh workflow run v54-pilot-runtime.yml --ref codex/v7-execution-wave6
gh workflow run v7-snapshot-recovery.yml --ref codex/v7-execution-wave6
```

Push triggers are also enabled: avoid duplicate dispatch if push already queued
the same SHA. Snapshot is a separate workflow, not an implied aggregate PASS.
