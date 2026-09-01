# Commercial P0/P1 integration result

Status: **CONDITIONAL**. Base: `2670f7da405c0023121938c205f3dce6601e473f` from `codex/mvp-safe-organizer-v1`.

## Integrated commits and merge bases

All four source commits have merge-base `ee2166ec5fca071d21d80d58e6a13507e7d4a773` with the selected base.

| Source | Integration commit | Content |
|---|---|---|
| `4f464c7aa2780a0dff1d038b7b8f9f2874ce3985` | `b57c83c` | durable job hardening |
| `a5a15c7f4454370ca0428c2f68abf280fbfe3b70` | `b92624d` | OCR evidence, confidence and review |
| `542356639105b10716fa123a2822714e0c0cea16` | `ee43e9d` | commercial legal kit |
| `7b847c3083d8dcfc8673cb953fd17cc393d859cf` | `4c28bbc` | Russian software registry dossier |

## Conflict map

- `backend/app/api/documents.py`: retained durable enqueue/status fields and OCR review/cancel API; cancellation now passes through the queue contract.
- `backend/app/schema.py`: selected merge revision `c83d0a24b512` without rewriting either source migration.
- `backend/tests/test_outgoing_email_completion.py`: retained both migration parents and asserted the merge node.
- Alembic: added no-op merge revision with parents `b71d2e4f9a10` and `b72c9f13a401`; one head remains.
- `docs/legal/09_RUSSIAN_SOFTWARE_REGISTER_READINESS_RU.md` and `docs/legal/README_RU.md`: retained the complete legal kit and added registry materials as linked extensions, without duplicate agreements.

## Integrated OCR/job contract

OCR job payloads carry project/document/job identifiers only, not file content. Cooperative progress and cancellation use the central queue API. A cancelled running OCR job finishes in canonical `cancelled`; evidence, per-page confidence and manual-review gates remain intact. Low-confidence OCR cannot authorize legal or financial actions.

## Verification record

- Backend pytest: `364 passed, 1 skipped` (the skip is an existing PostgreSQL-only environment gate).
- Frontend tests: `6 files, 17 tests passed`.
- TypeScript check: passed.
- Frontend production build: passed.
- Alembic heads: one, `c83d0a24b512`.
- `git diff --check`: passed (Git reports Windows line-ending notices only).
- Compose YAML: parsed successfully; services are `backend`, `db`, `scheduler`, `telegram-relay`, `worker`; backend starts two Uvicorn workers and Compose declares two durable workers.
- Docker executable and a local PostgreSQL server are unavailable in this environment.

The result is therefore `CONDITIONAL`, not `PASS`: real PostgreSQL migration/recovery and the multi-process failure protocol remain external gates.

## Mandatory external verification

Run from a clean checkout of the final commit with non-default test-only secrets:

```powershell
docker compose config
docker compose up -d --build db backend worker scheduler
docker compose ps
docker compose exec backend alembic -c alembic.ini heads
docker compose exec backend alembic -c alembic.ini upgrade head
docker compose exec backend pytest -q
```

Then use the protocol in `docs/audits/background-job-hardening-result.md` to verify: two API processes and two workers; repeated `Idempotency-Key`; API restart; worker kill during a leased job and recovery after lease expiry; full Compose restart; attempt exhaustion, dead-letter and redrive; OCR progress, cooperative cancel, evidence and manual review. Exercise downgrade only to each new migration's immediate parent on a disposable database, then upgrade back to `c83d0a24b512`; do not downgrade a production database.

## Deferred attachment staging

Local uploads and Gmail attachments are intentionally not moved to the queue. The required encrypted temporary-object lifecycle is specified in [encrypted-staging-design.md](../architecture/encrypted-staging-design.md).
