# Full-TZ closure increment, 2026-09-05

Base: `098df263df3b41f40005cdd82b55b90f5e87614d`.
Branch: `codex/mvp1234-wave2-integration`.

This is an implementation/acceptance increment, not a claim that the entire
specification is closed. The [full coverage map](tz-final-coverage-map.md) checks
the authoritative DOCX hash and all sections, including MVP6 and 1.0+, rather
than substituting the earlier narrow MVP5 pilot criteria for the whole product.
Its coverage baseline remains explicitly frozen at the SHA above.

## Integrated independent work

| Work | Source commit | Integrated commit |
| --- | --- | --- |
| PostgreSQL supply races and stale-session CAS fix | `00152469abfd56bce6f6f345c54fa36da3b9047c` | `73a27f23ba16ade39e77da7aaa164c0106424a14` |
| Remaining owned PostgreSQL acceptance gates | `e1735d53aaa2f84b41333138e424e04bc0f71809` | `aebd4e687d646961c9608f1a5906fba300b94aed` |
| Full specification traceability | `7c55cdc0acf27b26086a67b791b6c7789f0572f5` | `e9a3820e770f7a065db0efdb41520ca25f597e03` |
| Document filename AI policy | `5b377d82d5726db886c1cc4f2e0ecffe61ba92c3` | `092dc55a020953bd4a37c803a5410d21fbde0722` |
| XLSX formula/cache/cell extraction | `99f638033cd60e75cd0ce60c71f80f9048089fe9` | `9494281105c27b985b35ef15fa1db5f57941b643` |
| Deny unbound meeting authority | `7926421196fa458ff26d15ef595534333d7da312` | `61643efc744133f11a0b801c75eb374e6c908107` |
| Preserve Evidence authorization on meeting history reads | `4009bfbb4b8a1ae29f73434453072b42a288c3b2` | `847fdd709230b85b1bb5466f13303e4cfe62820a` |
| Bound sparse XLSX TSV projection after text cap | `772d27592ad66bf1e9ddcea448ae73ea93978c5e` | `f7fc07cb344790375ec96b3d885fd5be2aa04e88` |
| Preserve origin denial in UI and before POST | `7bd59dc127bc23491b670f22eab002f2c97d56e4` | `7a520140c0cfdda74d70a7621b5ae55caea825c4` |
| Reject HTTP 200 with explicit confirmation denial | `623b01a64320fc72928726f1804b7d624d931363` | `9f4f7033307faff505861d149e62b308fade8a2a` |

Root commits `c52e8aecc94bb09c293d277ba4e01c58be6b952c` and
`7f38f1895e3c9094bff9f9c1000007f482cb757a` correct false OCR acceptance,
require actual local-engine/POSIX evidence in an isolated CI job, and allow the
authority fixture's PostgreSQL service hostname only inside GitHub Actions.

The supply service now refreshes a locked ORM record before checking CAS. A
separate-session regression reproduced acceptance of a stale update before the
fix. Nine exact PostgreSQL races exercise real supply service transitions; they
do not place real orders, sign acts or confirm payments. See
[supply acceptance](mvp4-supply-postgres-acceptance.md).

The existing runtime runner now owns distinct authority migration/runtime,
materialization migration/runtime, local-upload and generic schema databases.
Empty migration fixtures are not pointed at already-migrated runtime databases.
All mandatory phases reject skip, missing target, xfail and deselection. See
[remaining runtime gates](mvp-runtime-remaining-gates.md).

OCR field/coordinate quality is now measured from the public extraction result,
not a second private OCR pass. Malformed/failed pages cannot inflate technical
success or disappear from the recall denominator. Historical OCR metrics are not
fresh evidence for this revised gate. See [local engine gate](local-engine-public-evidence-gate.md).

The Telegram document boundary applies the configured project policy to both
body and filename before passing them to AI or the cache. Redacted filenames
and metadata-only fixed labels replace the previously leaking raw context.
Original local names are preserved; no default policy or organization-wide DLP
was invented. The neighboring branch checks passed 73 tests with one local OCR
skip. See [filename guard](ai-egress-filename-guard.md).

The XLSX parser preserves separate formula/cached values and exact sheet/cell
locators, workbook relationship order and sparse columns. It does not evaluate
formulas or fetch links. ZIP/XML/resource limits fail with fixed error codes;
missing identities/cache are explicit review states. Its 165 branch-targeted
tests passed. This is extraction metadata, **not** a durable Evidence bridge:
ingestion persistence/source pins remain open. See [XLSX evidence](mvp1-xlsx-cell-evidence.md).

Independent review reproduced continued dense TSV expansion for sparse rows
after the text cap. A separate two-line correction gates construction itself,
not merely appending; later formula/cache/locator metadata remains preserved.
The new regression failed first, then **166 targeted tests passed**, including
54 XLSX cases. No timing threshold or skipped test hides the resource-bound bug.

Meeting CRUD/minutes/history remain available, but a meeting without a durable
SourceVersion binding cannot authorize a proposal, confirmation or Task/Decision
materialization. APIs expose an explicit invalid-source/review state rather
than pretending that arbitrary Evidence belongs to its mutable minutes. Review
also reproduced an overly permissive historical-read branch: the follow-up fix
restores the same current Evidence access/freshness check for both message and
meeting history. Revoked, stale and replaced sources deny access without deleting
history. The branch's six-module regression passed **60 tests**, without skips.
This safety fix does **not** complete M3-05: authoritative meeting source binding
and its CAS/human-binding API still need a coordinated implementation. See
[meeting origin evidence](mvp3-meeting-origin-evidence.md).

Independent integration review found no new public confirmation/Task-mapping
bypass in this slice (24 targeted tests passed). This is not a universal ACL
claim: generic history endpoints still use their existing project-role guards,
and future lifecycle callers must retain the meeting-origin check. Those
cross-cutting authority gaps remain in the full coverage map.

The existing UI previously discarded the new origin flags and could keep its
confirmation button enabled after an explicit server denial. A frontend-only
slice preserves/validates them, displays fixed safe explanations, disables the
control and checks again before POST. Unknown supplied origins fail closed;
legacy flagless message/manual responses remain compatible. A second review
reproduced a false-success case for HTTP 200 plus envelope/row denial: confirmation
parsing now rejects it before replacing state or displaying success. The real
App render explains that source binding is not implemented rather than implying
analysis can already authorize tasks. See [UI correction](mvp3-meeting-origin-ui-fix.md).

## Verification checkpoint

Product snapshot for the full backend/CI runs: `aebd4e687d646961c9608f1a5906fba300b94aed`.
The following documentation-only commit did not change the tested product.

- Full backend: **1640 passed, 36 skipped**, 33 existing Alembic warnings,
  575.52 s. The skips are 33 PostgreSQL checks, one local Tesseract benchmark
  and two Windows symlink checks. They are not acceptance passes.
- Full CI scripts, including Git Bash smoke contracts: **217 passed**, 88.65 s.
- Targeted OCR/commercial/batch: **27 passed, 1 Tesseract skip**.
- New local-engine harness/workflow contracts: **14 passed**.
- Authority fixture URL guards: **6 passed** (RED before fix).
- Actual local-engine wrapper: **FAIL**, 0 passed, 3 skipped; missing local
  Tesseract and Windows symlink permission, not Linux runtime evidence.
- Alembic: sole head **a54f001c0a18**; no new migration in this increment.
- Frontend unchanged: previous snapshot has 208 unit and 30 Chromium passes;
  these are carried-forward results, not claimed as a new execution.
- Actual PostgreSQL, Linux OCR/POSIX job, actionlint and provider execution:
  **NOT_RUN** locally. No production or provider credentials were used.

Integrated snapshot `847fdd709230b85b1bb5466f13303e4cfe62820a`:
full CI scripts rerun **217 passed**, 95.06 s; complete backend rerun
**1727 passed, 36 skipped, 33 warnings**, 563.43 s. These are actual integrated
executions, not sums of branch totals. The following XLSX cap correction adds
one regression and is verified against its own final product snapshot below.

### Final integrated verification

Backend product snapshot: `f7fc07cb344790375ec96b3d885fd5be2aa04e88`.
The following two commits are frontend/docs only; no backend file changed during
this final full run.

- Full backend: **1728 passed, 36 skipped, 33 warnings**, 745.66 s. Skip categories
  remain 33 PostgreSQL, one local Tesseract and two Windows symlink checks.
- Final UI snapshot: `9f4f7033307faff505861d149e62b308fade8a2a`.
  After its integration, all backend tests referencing frontend/App/module paths
  were rerun: **88 passed**, 5.59 s.
- Full integrated frontend: **26 files, 236 passed**, 13.37 s.
  TypeScript `check` and build passed. Build used the existing no-env E2E Vite
  configuration and `node_modules/.cache/tz-closure-build` as output; tracked
  `react_dist` was not modified. The existing >500 kB chunk warning remains.
- The initial sandboxed Vitest config load failed with directory access denial;
  an authorized local rerun succeeded, without application/config changes.
- CI scripts remain unchanged from the recorded **217 passed** integrated run.
- Alembic remains the sole `a54f001c0a18` head, matching schema readiness.
- Integrated Chromium suite: **30 passed**, 37.4 s, with synthetic intercepted
  API responses and the harness-owned loopback server. This is real browser
  execution, not live provider or real backend acceptance. No retries were used.
  The new meeting-specific denial cases are additionally covered by the real-App
  Vitest render tests, not claimed as new live-browser/backend scenarios.
- Final report/source-link validation: **277 links checked, zero missing**.
  Diff check passed; `react_dist` has no changes in this increment. The loopback
  test port 4179 did not accept a connection after Playwright completed.

Commands (from the indicated repository subdirectory):

```powershell
# backend; use the existing shared test virtualenv's Python
python -X utf8 -m pytest tests -q -rs --tb=short --basetemp=.pytest-mvp-closure-final-xlsx-cap
# frontend
npm.cmd run test
npm.cmd run check
npm.cmd run build -- --config e2e/vite.config.mjs --outDir node_modules/.cache/tz-closure-build
$env:PUW_E2E_EXTERNAL_SERVER = '0'
npm.cmd run test:e2e
```

Run `python -m alembic heads` from `backend`: an incidental root-directory
invocation using `-c backend/alembic.ini` failed because its relative `migrations`
path was resolved from the wrong working directory. The corrected command passed;
no migration file/config was changed to mask this command error.

## Isolated CI handoff (not executed)

From this exact integration worktree, after approval of the final SHA:

```powershell
git status --short
git rev-parse HEAD
git push origin HEAD:refs/heads/codex/mvp1234-wave2-integration
```

The existing workflow has a push trigger for this branch. Confirm the new run's
head SHA before evaluating it; do not compare an older artifact to this candidate.
If a manual run is needed instead, after publication and with GitHub CLI installed:

```powershell
gh workflow run v54-pilot-runtime.yml --ref codex/mvp1234-wave2-integration --repo bigbrotherdmitriy-prog/pu-workspace
```

Avoid dispatching a duplicate manual run while the push-triggered run is active.
No production configuration is needed. Require both PostgreSQL runtime and
local-engine jobs plus their safe protocols; an absent, skipped or failed phase
is not PASS. No raw stdout/stderr, DSN, document text or provider credentials are
added to the protocol artifact. This is not a new release-bundle security audit.

## Remaining boundaries

Source/code gaps, actual-runtime gates and owner decisions are separate:

- A real PostgreSQL CI run must prove the new migration/concurrency targets and
  cleanup on the candidate SHA; static runner tests do not establish this.
- Drive mutation clients still lack proven atomic provider preconditions and
  live reconciliation. Disabled capability is a safety measure, not completed
  rename/move functionality.
- The coverage map identifies spreadsheet/native-export evidence, exact meeting
  origin, durable escalation, inbound Calendar/Tasks state and cross-cutting
  organization authority/audit/AI-egress gaps. Each requires a bounded change.
- Isolated live-provider accounts, owner/accounting choices and legal documents
  cannot be supplied by inventing data or activating production.

### Next bounded implementation slices

1. M3-05: typed, append-only meeting-to-existing-SourceVersion binding, a Meeting
   CAS counter, human binding command, and origin reference. Use one forward
   migration after a18; retain NULL/unbound legacy history with no inferred
   backfill. Check exact binding/version again at proposal, confirmation and
   Task creation, including generic APIs. The source-selection UI and actual
   PostgreSQL bind/edit/confirm races are required for end-to-end closure.
   This is a proposed next slice, not a schema introduced by this increment.
2. D14: persist the new sheet/cell representation against an exact immutable
   SourceVersion and current access/retention checks; do not label parser-only
   coordinates as durable Evidence or assert cached formulas are current.
3. D05/M303/M207: native export/cache identity, durable escalation and inbound
   Calendar/Task corrections need their own scoped, regression-first increments.
   They can run in separate worktrees with one integration owner for migrations.

No frontend or provider activation should be based solely on the local safety
denials or structural gate passing. The complete source-linked coverage map
remains the inventory, including MVP6/1.0+ and owner-dependent requirements.

No push, merge, PR, production deployment or real external message occurred.
Original dirty worktree and production remain outside this increment's writes.

## Decision and handoff

**CONDITIONAL** for this local integration candidate. **The full TZ is not closed.**
Runtime/live/owner gates and the listed implementation gaps cannot be converted
to PASS by adding synthetic test counts. This report and the coverage map are the
saved continuation point. Prior permissions for older SHAs are not treated as
publication approval for this candidate.

All source commits are preserved; cherry-picks were conflict-free. Product
snapshot is `9f4f7033307faff505861d149e62b308fade8a2a`; the final report commit adds
only this report and a link from the earlier integration checkpoint. Obtain its
exact full SHA with `git rev-parse HEAD` after the documentation commit.

Complete changed-file inventory and ordered history, from repository root:

```powershell
git diff --name-only 098df263df3b41f40005cdd82b55b90f5e87614d HEAD
git log --reverse --format='%H %s' 098df263df3b41f40005cdd82b55b90f5e87614d..HEAD
git diff --check 098df263df3b41f40005cdd82b55b90f5e87614d HEAD
git status --short
```
