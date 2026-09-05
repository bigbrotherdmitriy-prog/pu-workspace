# MVP3 Management Center: scope and bounded-query acceptance

Date: 2026-09-05

Branch: `codex/mvp3-scope-performance-acceptance`

Base: `4434434de4ab52b33d1962345c6056d19e5eecd7`

## Decision

**PASS for synthetic SQLite acceptance; PostgreSQL runtime remains CONDITIONAL.**

The acceptance corpus uses more than 1,000 synthetic records and no live provider,
customer document, mailbox, production database, token or secret. Assertions are
based on result bounds and SQL statement counts, not wall-clock thresholds.

## Baseline gaps and minimal corrections

| Area | Baseline gap | Correction |
|---|---|---|
| Attention | All rows per entity type were materialized before pagination | Each type is capped at 1,000 + one sentinel row; response reports `scan_truncated` and the cap |
| Attention evidence | Stored JSON pins were returned without validating tenant/project or unknown fields | Strict `VersionPin` parsing plus one project-scoped Evidence/Source query; output is reconstructed from the allowlisted DTO |
| Obligations, risks, decisions, contacts | List endpoints had no pagination | Maximum page size 200, default 100, exact count and `has_more` |
| Contracts | List endpoint was unbounded and performed multiple queries per contract | Bounded page plus batched versions, documents and aggregate analysis queries |
| Project search | Contract and evidence resolution performed queries per result row | Bounded scans are resolved in batched project/tenant-scoped queries |
| Digest scheduler replay | One idempotency lookup per preference | One bounded preference query and one bulk idempotency lookup for up to 1,000 preferences; larger cohorts fail closed |

No schema change was necessary: every affected model already has project and
relationship indexes. No migration was added. The single Alembic head remains
`a54f001c0a17`.

## Acceptance contract

`backend/tests/test_mvp3_scope_performance_acceptance.py` verifies:

- 1,000 obligations plus 250 risks, 250 decisions, 250 contracts and 250 contacts;
- strict project/tenant isolation, including a second synthetic tenant;
- viewer reads, editor denial for manager mutation, manager mutation;
- page size and invalid-bound enforcement;
- constant SELECT budgets: simple lists no more than 3, contract page no more
  than 14, multi-type search no more than 10, digest replay exactly 2;
- attention scan cap and truncation signal;
- strict removal of an injected `provider_payload` field;
- no `ProviderAction` and `external_actions_created=false`;
- digest replay over exactly 1,000 persisted preferences/jobs.

The existing browser suite already covers stale responses during project switch.
This backend acceptance ensures the switched project cannot obtain rows, evidence
or provider material from a different project/tenant.

## Verification

Commands were run from `backend/` with the repository test interpreter.

```powershell
python -m pytest tests/test_mvp3_scope_performance_acceptance.py -q
# 3 passed

python -m pytest tests/test_mvp3_management_acceptance.py `
  tests/test_mvp3_foundation_lifecycle.py tests/test_mvp3_digest_preferences.py `
  tests/test_mvp3_search_saved_views.py tests/test_mvp3_contract_versions.py `
  tests/test_project_contacts.py tests/test_contracts_api.py -q
# 63 passed

python -m pytest -q --basetemp .pytest_tmp_mvp3_scope
# 1387 passed, 23 skipped

python -m alembic -c alembic.ini heads
# a54f001c0a17 (head)
```

The first full-suite attempt used the shared Windows `%TEMP%` while other tasks
were active and ended with 249 fixture setup errors (`PermissionError`) after
1,142 passes. Re-running with an isolated repository-local `--basetemp` completed
successfully. This was an environment collision, not a product test failure.

`python -m compileall -q app tests/test_mvp3_scope_performance_acceptance.py`
and `git diff --check` also passed.

## Remaining limits

- PostgreSQL query plans, concurrent writers and actual latency were not measured;
  this commit intentionally makes no PostgreSQL runtime PASS claim.
- The search scan cap is 1,000 records per entity type. `scan_truncated=true`
  requires the caller to narrow filters; it is not an exhaustive export API.
- The digest scheduler rejects more than 1,000 eligible persisted preferences in
  one pass instead of silently starving later rows. Horizontal cursor batching is
  future operational work if a deployment exceeds this cohort size.
- The legacy meetings, open-issues and notifications endpoints were outside this
  Management Center acceptance slice and remain candidates for a separate bounded
  API review.

## Files

- `backend/app/api/governance.py`
- `backend/app/api/management.py`
- `backend/app/api/organizations_contracts.py`
- `backend/app/api/project_contacts.py`
- `backend/app/mvp3/attention.py`
- `backend/app/mvp3/meeting_digest.py`
- `backend/app/mvp3/search.py`
- `backend/tests/test_mvp3_scope_performance_acceptance.py`
- `docs/audits/mvp3-scope-performance-acceptance.md`
