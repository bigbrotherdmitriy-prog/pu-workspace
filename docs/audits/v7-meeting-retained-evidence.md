# V7 meeting source: retained child evidence after original purge

Date: 2026-09-08. Branch: `codex/v7-meeting-evidence-wave2`.
Base: `4bcc94ab113f544a88ed9eaeb7ebd37b6133b410`.
Scope: one backend service, dedicated synthetic tests, this report.

## Audit and regression

The meeting service required a live DERIVED original for every catalog, binding,
proposal and confirmation check. Durable XLSX ingestion correctly purges its
original after publishing independently retained encrypted cell evidence. The
result was an empty meeting catalog and rejected binding, even though the exact
child remained authorized and readable through the existing fragment contract.

Initial regression: **4 failed** (catalog empty / bind denied). A preceding test
attempt failed in pytest temporary-directory setup; it is not product evidence.
Tests were rerun with a worktree-local `--basetemp`.

## Change and security boundaries

- Source metadata first passes the existing exact A05 source authority boundary.
  Completed PURGED originals are accepted only after a separately authorized
  retained XLSX child is found. At most 513 proof candidates are inspected
  (512 child capacity plus the original); overflow fails closed.
- Every requested evidence pin is independently rechecked. Another available
  child cannot substitute for an expired, purged or mismatched requested child.
  Original-only and mixed original/child pins after original purge are denied.
- Reuse `require_local_xlsx_fragment` and `ProductEvidenceResolver`: exact
  original/version identity, child parent/owner/project, manifest/storage linkage,
  policy/authority, current version, retention and assessment are checked.
  Canonical full ObjectRef/VersionPin equality is required. Descriptor does not
  have a locator field; extra locator data is rejected. Immutable evidence locator
  remains the single cell locator used by the shared lineage contract.
- Metadata fallback does not flush pending changes. New retained-child checks
  conservatively reject pending Evidence/Assessment/Materialization changes
  before refreshing those rows. The pre-existing public `_meeting` entrypoint
  still has its caller-owned transaction/autoflush behavior; this change does
  not claim a general pending-session hardening of all management entrypoints.
- No content is read, copied or rehydrated. Tests replace storage `read_chunks`
  with a hard failure. `confirmation_available` means source association can
  proceed to per-pin/human checks, not that original bytes are available.
- No implicit verification is added. Unverified retained proof produces no Task
  until explicit human confirmation. Repeated confirmation creates one Task.
- No shared authority, fragment reader, models, migrations, APIs or UI changed.

## Checks

From `backend`, using the workspace test Python 3.12:

```powershell
& '../../.venv-pu-workspace-tests/Scripts/python.exe' -m pytest -q tests/test_v7_meeting_retained_evidence.py tests/test_mvp3_meeting_source_binding.py tests/test_mvp3_meeting_digest.py tests/test_mvp1_xlsx_durable_evidence.py tests/test_mvp1_local_source_authority.py tests/test_v7_xlsx_retention_recovery.py --tb=short --basetemp=.pytest-meeting-final
```

**133 passed, 1 skipped, 61.87 s.** The skip is the pre-existing real PostgreSQL
retention-lock scenario; no isolated PostgreSQL URL was configured. After adding
the positive explicit-human-confirmation/replay scenario, the final dedicated
file was rerun: **18 passed, 13.14 s**. Earlier meeting/digest/XLSX neighbors:
**84 passed, 32.95 s**. Counts overlap and must not be added together.

`git diff --check`: PASS. No migrations added; inherited head remains `a54f001c0a19`.
No full backend, frontend, browser, Docker, live provider or PostgreSQL runtime
claim is made for this isolated change.

## Remaining scope / acceptance

**CONDITIONAL** for integration/runtime acceptance; synthetic scoped behavior is
proven. Only the existing XLSX retained-cell lineage is supported, not arbitrary
PDF/audio/OCR child formats. A real meeting protocol importer and its verified
semantic assessment remain separate work. No HTTP/UI end-to-end acceptance or
original plaintext integrity read occurs in this metadata-only operation.
PostgreSQL concurrent purge/confirmation needs the isolated runtime gate; no
production source, key, database or document was accessed.

Changed files:

- `backend/app/mvp3/meeting_source_binding.py`
- `backend/tests/test_v7_meeting_retained_evidence.py`
- `docs/audits/v7-meeting-retained-evidence.md`

Original dirty worktree untouched. No push, merge, PR or deployment performed.
