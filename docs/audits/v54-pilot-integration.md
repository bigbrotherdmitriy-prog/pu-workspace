# v5.4 synthetic CONFIRM integration

Date: 2026-09-03. Branch: `codex/v54-pilot-integration`.

## Verdict

**CONDITIONAL — real A/B/C composition and local transactional regression pass;
PostgreSQL process-fault/migration acceptance NOT RUN. Production enable BLOCKED.**

This is an isolated backend integration, not a production pilot launch, UI/API
release, durable authorization implementation or external exactly-once proof.
No push, PR, merge, workflow dispatch, VPS access or deploy was performed.

## Base, history and initial audit

Exact foundation: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
Worktree: `pu-workspace-v54-pilot-integration`. The branch did not exist and was
created from the requested foundation, not 66129dc. All three inputs were present;
their merge-base with foundation was the exact foundation SHA. No applicable
AGENTS.md was found in the inspected workspace/repository hierarchy.

| Order | Input | Cherry-pick in this branch |
|---|---|---|
| A | `7674e973401301d4d31e8561ce7875427a600869` | `5d46cdaaedcad503cef1c8c200a01e2eb66f4f00` |
| B | `7edea2b5e6b362b856dfb752ee4a09ae598e12d2` | `b7b7a547eeaa304234a288a39616d4f54262e931` |
| C | `f384ae533d6ac48229d2bf00aa2659b8b3895ca6` | `1a15fb4822f2b536432b6df6235adc72160366c1` |

All picks were conflict-free; no source branch was rewritten. The final separate
integration commit follows these three commits (full SHA in handoff; not embedded
self-referentially here).

Main dirty worktree remained on `codex/commercial-p2-yandex360`, SHA
`83774aac726acd4e27b349e9194f30783158bde8`. Its pre-existing modified files:
`backend/app/api/auth.py`, `backend/app/api/local_upload.py`,
`backend/app/api/workspace.py`, `backend/app/schema.py`,
`backend/app/static/app.js`, `docker-compose.yml`, `frontend/index.html`.
None was copied, overwritten or committed.

Read foundation/A/B/C reports and interface requests, integration architecture,
common DTO/refs/interfaces/transactions, models, fixture and queue/worker sources.
Initial findings: A resolves only Source/Version/Evidence; B/C require additional
domain pins; B and A have circular first-mailbox bootstrap prerequisites;
TaskMutation/queue bridge were test doubles/missing; authority was explicitly
in-memory synthetic; two CI expectations still referenced pre-foundation head.

## Interface map, defects and changes

| Boundary / request | Resolution |
|---|---|
| A resolver → B/C, B Message version bridge | `SyntheticResolver` delegates actual source pins to A; checks actual domain rows, tenant/project/mailbox/identity and versions. Message pin explicitly means context_version. Unknown types and fragment access deny. |
| A requires mailbox before source; B requires source before mailbox | Additive B-owned `bootstrap_mail_connection`, requiring exact namespace authorization callback and verified identity; no callback means deny. No fake source/project; blocked existing mailbox is not reactivated. |
| B primary relation scope vs C validation | Real end-to-end test failed in `validation.live_pins`: B wrote mailbox scope, C required project. B now stores target project scope (including contract's project); C assertions remain unchanged. Mailbox remains in source origin. |
| C TaskMutation request | Real `InternalTaskMutation` joins caller transaction, repeats binding/grant/lease/pin/assignment checks, writes Task + TaskHistory only. It sets message_id so B can consume the real receipt. No Obligation, commit, provider or notification. |
| C pending recovery request | `SyntheticDispatch`: separate T1 read, enqueue session, link session; replay stable queue key and validate kind/tenant/action/revision. Worker may safely link a matching unmarked job before T2. |
| C global command namespace request | `synthetic_command_key` uses tenant/action UUID/revision, never source content. Harness uses it. Legacy caller keys are not rewritten; collision with another queue kind/action denies linking, not reassignment. |
| Worker ownership snapshot | Existing execution context extended compatibly with attempt + locked_at; old consumers keep their original two-field context. Worker passes claim-time values. Pilot rejects missing snapshot; does not reconstruct ownership from current DB state. |
| Authority/locking requests A1/B/C1 | Existing intake Project lock is acquired first across composition; fine-grained owner locks follow. This serializes synthetic intake-project operations, NOT a durable real ACL registry. Production remains blocked. |
| Schema/CI request | Two actual runtime head checks updated to `a54f001c0a01`; historical migration predecessors retained. No DTO/model/schema/migration change. |

Before fixes, new regression checks failed for stale CI head and missing real
TaskMutation (2 failed). During real composition, the scope mismatch failed before
its fix. No allow-all resolver, recording Trust or fake TaskMutation remains in
the new end-to-end test. SyntheticPolicy, synthetic actors/date/metadata remain
explicit test inputs, not real security authorization or extracted document facts.

## Transaction, recovery and lock protocol

1. A writes actual identity/source observations/evidence; B bootstraps mailbox and
   registers actual synthetic Message with one attachment reference. Claim human
   review, context confirmation and action approval are distinct operations.
2. B hands the exact envelope to C.freeze. T1 persists sealed revision, separate
   approval and PendingDispatch. Caller commits before queue enqueue.
3. Dispatcher reads/rechecks committed intent. Queue.enqueue runs in a different
   Session because it commits. Link stores original job_id only after matching
   immutable seal/revision/approval. Replays never mint a bypass key. Terminal
   jobs are not automatically redriven; existing authorized operator flow applies.
4. Scheduler calls recovery only when a synthetic runtime was explicitly installed.
   Recovery scans pending records with no job_id. If worker arrives before the
   marker, it can bind the exact queued job in T2; changed/revoked intent still
   fails Trust checks. Scan is PendingDispatch recovery, not a second queue.
5. T2: project authority guard → action/policy → context/source/claim → Task and
   existing job lock. Trust checks worker_id, attempts, locked_at, live lease and
   cancellation; mutation repeats current permissions and target checks. Task,
   TaskHistory, ActionReceipt and append_audit commit together or roll back.
6. Context consumer is a separate transaction after receipt commit. Failure cannot
   undo/repeat Task. Retry reads the same receipt and retries unique projection.
7. Cancel is a different action/approval, targeting assigned Task exact version.
   It rejects external publication or an existing Obligation. Original receipt
   remains; a second cancellation receipt and TaskHistory are appended.

No existing Task endpoint or task_engine helper with hidden commit is called.
No second ledger, queue, reservation-token schema or receipt-attempt journal added.
Queue payload has only tenant_id/action_id/revision/correlation UUID. Result has
receipt ID only. Audit uses the original append_audit and no document details.

## Tests and environment

Commands use existing `.venv-pu-workspace-tests/Scripts/python.exe`; backend tests
explicitly set `DATABASE_URL=sqlite+pysqlite:///:memory:`. No production env file
or credentials were used.

| Check | Actual result |
|---|---|
| Full backend regression, first run | 738 passed, 7 skipped, 4 existing Alembic configuration warnings; 144.40 s |
| Full backend after final additions | 740 passed, 7 skipped, 4 existing warnings; 137.76 s |
| New integration suite | Final repeat: 20 passed, 25.54 s after fixture-cleanup hardening; SQLite with real separate Sessions, NOT PostgreSQL |
| Targeted real A/B/C + integration | 175 passed, 50.99 s |
| Existing scripts/ci/tests + scripts/ci/durable_queue | 81 passed, 4.28 s |
| frontend check | PASS (`tsc --noEmit`) |
| frontend test | 44 passed / 8 files; 12.18 s after permitted sandbox escalation |
| frontend build | PASS; 1616 modules transformed. Generated react_dist output reverted; not shipped in this commit |
| actionlint 1.7.12, changed docker-smoke workflow | PASS; shellcheck/pyflakes auxiliary executables disabled, native actionlint checks enabled |
| Alembic heads | Exactly `a54f001c0a01 (head)`; schema constant matches |
| Documentation validator | PASS: 37 records, 2 actions, 4 mutation checks |
| Compose queue config --quiet | PASS using explicit empty env file and synthetic values including QUEUE_CI_IMAGE |
| git diff --check | PASS |

Counts overlap and are NOT summed as unique tests. Seven existing skips are:
one opt-in generic PostgreSQL schema check, four B concurrent PostgreSQL tests,
one foundation PostgreSQL upgrade/downgrade test, one A PostgreSQL CAS test.
No new skip was added. Offline foundation SQL generation is not online migration.

Initial `pnpm install --offline` lacked Playwright's cached tarball. Normal pnpm
regression invocation subsequently installed the missing pinned dependencies.
Vitest first failed on sandbox parent-directory access, then passed with scoped
permission. No frontend source or lockfile changes. An initial Alembic invocation
from repo root failed on relative migrations path; rerun from backend passed.
Initial Compose config lacked QUEUE_CI_IMAGE; explicit synthetic value fixed it.

Docker full-path probe was denied inside sandbox, then retried read-only with
permission: CLI exists; daemon query timed out at 10 s. Docker is NOT absent.
Read-only host checks: free disk 30.74 GiB, free RAM 1,512,436 KiB at inspection.
No Docker/WSL restart or cleanup of other work occurred. No explicitly isolated
PostgreSQL connection was supplied. Local build/process runtime was not attempted.

### Fault coverage actually executed

SQLite tests execute real bootstrap → observe/review → Context confirmation →
Claim review → B-to-C freeze → approval → enqueue/claim → T2 → Task/receipt/audit →
Context projection → separately approved cancellation. They check duplicate
ingress/receipt/consumer, missing queue marker recovery, queue-key collision,
stale worker/attempt/lease, disabled execution, revoked identity, changed context,
extra payload content rejection, audit failure rollback and post-T2 consumer crash.
Existing A/B/C tests cover further source freshness, claim/payload/approval changes,
late analysis, cross-scope and fake external UNKNOWN contracts.

There are **no real PostgreSQL fault job IDs/results** in this report. Sequential
test IDs are not presented as process-fault evidence. API/Compose restart,
wall-clock lease expiry, concurrent authority revoke, backup/restore and distributed
deadlock absence have NOT been proved for this integrated pilot.

## Runnable PostgreSQL validation, NOT EXECUTED

New `scripts/ci/v54_pilot_runtime.py` explicitly refuses a missing test DB URL and
never falls back to SQLite. It reuses the real integration fixture/composition,
creates a unique schema, launches a claiming process, verifies a second process
cannot claim its live job, kills only its own first child, accelerates lease expiry,
rejects stale ownership and lets a replacement process execute via real handler.
It asserts one Task/receipt and completed queue job, then drops only its random
schema. Output is safe JSON lines with IDs/status; errors expose class only.

This probe is prepared but unvalidated on PostgreSQL. It uses accelerated expiry,
not a claim of elapsed wall-clock expiry. It is not the full API/Compose fault gate.
`PUW_V54_INTEGRATION_DATABASE_URL` must name `puw_v54_test_*`, host
localhost/127.0.0.1/::1/db, with no connection query overrides. New tests can also
run on PostgreSQL with this explicit variable; they do not replace migrations.

Linux isolated-runner commands (future authorized run; fresh CI DB, no published
host ports, no production credentials; no xtrace):

```bash
set -euo pipefail
set +x
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_hex(24))')"
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export BOOTSTRAP_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(24))')"
export TOKEN_ENCRYPTION_KEY="$(python -c 'import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
export DATABASE_URL="postgresql+psycopg://puw_ci:${POSTGRES_PASSWORD}@db:5432/puw_v54_test_runtime"
export PUW_V54_INTEGRATION_DATABASE_URL="$DATABASE_URL"
export GMAIL_AUTO_SYNC_ENABLED=false AI_SECRETARY_AUTOMATION_ENABLED=false
tag="puw-v54-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
network="$tag-net"; volume="$tag-db"; db="$tag-db"; runner="$tag-runner"
# Fail rather than reuse any existing resource with these names.
if docker network inspect "$network" >/dev/null 2>&1 || \
   docker volume inspect "$volume" >/dev/null 2>&1 || \
   docker container inspect "$db" >/dev/null 2>&1 || \
   docker container inspect "$runner" >/dev/null 2>&1; then
  echo 'Refusing pre-existing test resource names' >&2; exit 1
fi
docker network create --internal "$network"
docker volume create "$volume"
cleanup() {
  docker rm -f "$runner" "$db" >/dev/null 2>&1 || true
  docker volume rm "$volume"
  docker network rm "$network"
}
trap cleanup EXIT
docker build -t "$tag:backend" ./backend
docker run -d --name "$db" --network "$network" --network-alias db \
  --memory 512m --cpus 1 -e POSTGRES_PASSWORD -e POSTGRES_USER=puw_ci \
  -e POSTGRES_DB=puw_v54_test_runtime -v "$volume:/var/lib/postgresql/data" \
  --health-cmd='pg_isready -U puw_ci -d puw_v54_test_runtime' \
  --health-interval=2s --health-retries=30 postgres:16-alpine
for n in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$db")" = healthy ] && break
  sleep 2
done
test "$(docker inspect -f '{{.State.Health.Status}}' "$db")" = healthy
# Empty dedicated DB only: predecessor -> head -> predecessor -> head.
docker run --rm --network "$network" -e DATABASE_URL --entrypoint sh "$tag:backend" -c \
  'alembic upgrade f360a1b2c3d4 && alembic upgrade head && alembic downgrade f360a1b2c3d4 && alembic upgrade head'
docker run --name "$runner" --network "$network" --memory 1536m --cpus 2 \
  -e DATABASE_URL -e PUW_V54_INTEGRATION_DATABASE_URL -e APP_SECRET_KEY \
  -e BOOTSTRAP_TOKEN -e TOKEN_ENCRYPTION_KEY -e GMAIL_AUTO_SYNC_ENABLED \
  -e AI_SECRETARY_AUTOMATION_ENABLED -e PYTHONPATH=/app \
  -v "$PWD/backend/tests:/pilot/backend/tests:ro" \
  -v "$PWD/docs/architecture/v54/integration:/pilot/docs/architecture/v54/integration:ro" \
  -v "$PWD/scripts/ci/v54_pilot_runtime.py:/pilot/scripts/ci/v54_pilot_runtime.py:ro" \
  --entrypoint python "$tag:backend" /pilot/scripts/ci/v54_pilot_runtime.py
```

Use a clean checkout. The backend Dockerfile is unchanged and does not include
tests; mounts above supply only test sources/fixture/probe, not .env or client data.
Build memory is not constrained by container `--memory`: require a dedicated
runner with adequate build resources (not this ~1.4 GiB-free host or production).
Cleanup must additionally be verified by exact-name inspect/list assertions in
the future CI job; no cleanup PASS is claimed here. No image prune/global down.

## Enable/disable and remaining blockers

Product startup installs no runtime. Handler defaults to
`pilot_authority_not_configured`; scheduler does nothing for pilot without explicit
installation. Only a synthetic composition root constructs `SyntheticComposition`
with an explicit policy and `enabled=True`, then installs `SyntheticDispatch`.
Setting enabled=False forbids new executions and enqueue; receipts/history remain.
No disabled/pending/UNKNOWN work falls back to legacy execution. PostgreSQL runtime
constructor refuses databases outside `puw_v54_test_*`.

Still required before any real cohort:

- Durable Source/authority/retention assignment and revocation epochs with all
  writers in the same lock protocol. In-memory snapshots are NOT production grants.
- Real legacy routes must route/deny pilot Task writes; no pilot HTTP writer is
  exposed now. A DB-name restriction is defense in depth, not real-data permission.
- Global uq_message_source and required project remain unchanged. Cross-mailbox
  collisions still fail closed; no salting or fake common project/backfill.
- Distinct identity/reject/recheck audit semantics, retention/purge/replay and DB
  role enforcement remain open A requests. Current events are not a complete legal
  decision trail; evidence rejected remains unverified in the existing contract.
- Fragment materialization, OCR/staging/no-copy cutover are not integrated; no
  bytes/provider/model calls were added. Existing storage paths not rewritten.
- PostgreSQL migrations, full two-worker faults, concurrent revoke/deadlocks and
  restart/backup/restore/cleanup remain runtime gates. Probe has not run yet.
- AUTO, external execution, finance effects and external exactly-once remain off.

No new migration is required by these changes. Next authorization should name the
final candidate SHA and allow pushing only that branch/candidate and running the
existing CI plus the isolated PostgreSQL probe. This report does not grant push,
dispatch, merge or production permission. CI alone does not close owner decisions.

## Integration files

- `.github/workflows/docker-smoke.yml`
- `backend/app/context_communication/service.py`
- `backend/app/jobs/handlers.py`, `queue.py`, `scheduler.py`, `worker.py`
- `backend/app/pilot_composition.py`
- `backend/app/pilot_dispatch.py`
- `backend/app/pilot_task_mutation.py`
- `backend/tests/test_v54_pilot_integration.py`
- `scripts/ci/durable_queue/run.py`
- `scripts/ci/v54_pilot_runtime.py`
- `docs/audits/v54-pilot-integration.md`
