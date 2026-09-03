# Additive migration plan и следующая волна

Это план будущих заданий, не разрешение на coding/deploy. SQL/ORM/Alembic в
этом пакете нет. Единственный владелец всех DB migrations — интегратор схемы.
Ни один параллельный поток не создаёт собственную Alembic revision.

## Последовательные gates

1. Подтвердить O2–O5 для implementation; O1 нужен лишь для AUTO, которое остаётся
   выключенным. Согласовать versioned resolver/transaction interfaces и allowlist.
2. На новой интеграционной базе инвентаризировать legacy IDs/collisions/pending
   jobs без контента; проверить ровно одну Alembic head. У базы этого документа
   ожидается f360a1b2c3d4; перед каждой будущей миграцией сверять фактическую head,
   не создавать параллельных веток и не переписывать исторические migrations.
3. Additive identity registry + mailbox extension/tenant constraints/legacy bridge.
   Existing PK, nullable origins, current manual relations сохраняются. Нельзя
   выводить mailbox из current project. Collision report до смены global unique.
4. Additive Source/Version/Evidence/assessment и narrow claim revision records;
   ContextRelation/Message context CAS. FK/polymorphic resolver проверяет tenant.
   Никакого массового backfill/source I/O. Только явный synthetic seed.
5. Additive Action revision/approval/receipt/reservation/pending_dispatch +
   AuditLog extension (один writer); target record_version/transaction facade.
   Unique intent/command/receipt, history RESTRICT/tombstones, не cascade стирание.
6. Shadow reads synthetic cohort: old reader authoritative; новый сравнивает
   hypotheses/policy только read-only, не Task/send. Не dual master: когда включены
   записи, один cohort router выбирает writer, старые routes этого cohort → facade/deny.
7. После review и PostgreSQL fault gates включить CONFIRM task create/cancel
   для synthetic cohort; сверить browser→API→worker→receipt→ledger. Отдельно
   разрешать real data/cohort, не по факту успешной сборки.

Предлагаемые flags (не env текущего продукта): v54_reference_shadow,
v54_context_pilot, v54_task_confirm, v54_external_execute=false,
v54_task_auto=false. Unknown/missing flag deny нового execution; allowlist tenant/
project/mailbox обязательна. Не выключать прежние integrations глобально.

Rollback: запрет новых pilot ingress/dispatch; сохранить readable history/receipts,
разрешённый recovery/reconcile, завершение атомарной T2; не отдавать UNKNOWN в
legacy send и не удалять jobs/Task/history. Более строгий no-copy/revoke остаётся
активным при rollback readers. DDL downgrade с удалением данных не предлагается.

## Runtime / staging зависимости

Runtime-коммит `531bd25a918248f97f20fd04bbb5eac25688935f` не перенесён.
Его отдельный CI review должен доказать PostgreSQL, two API/two workers/scheduler,
lease/crash/retry/restart/backup-restore/cleanup. Данный контракт не закрывает
этот gate и не заимствует статус PASS. Синтетическая идемпотентность не exactly-once
Gmail/Drive. CI зелёный build сам по себе не доказательство action atomicity.

Staging fork `372b661eefebb9c154dd847e8c331acc2b128d94` не включён в базу/эту ветку.
Если понадобится materialization, отдельная интеграция его schema/queue/volume/
retention/cancellation и runtime. Reference-only не требует staging. Encryption
не разрешает запрещённые копии; существующий snapshot→safe-copy требует отдельного
policy cutover у workspace owner. Без него нельзя заявлять federated no-copy.

## Параллельная волна после gates 1–2

Ниже будущие точные ownership paths; новые пути — предложение для выдачи заданий,
они сейчас не создаются. Shared files назначены только одному исполнителю.

| Исполнитель | Исключительная область записи | Зависимость / handoff |
|---|---|---|
| DB/contract интегратор | backend/app/models/{connection_identity,source_reference,evidence,context_relation,deadline_claim,action_trust}.py; backend/app/models/{ai_secretary,task,audit_log}.py; backend/migrations/**; backend/app/schema.py; backend/app/core/v54_refs.py | Один schema writer, замороженный wire, ORM+DTO interfaces; одна head; остальные передают schema requests |
| Identity/security | backend/app/integrations/connection_identity.py; backend/app/core/{v54_permissions,auth}.py; backend/app/api/integrations.py; backend/tests/test_v54_identity.py | O2–O4, registry schema; account/generation+ACL epoch resolve, no credentials fixtures |
| Source/Evidence | backend/app/source_evidence/**; backend/tests/test_v54_source_evidence.py | Identity/permission stubs до готовности; source/version/assessment resolve; existing OCR/storage только читать |
| Context/Communication | backend/app/context_communication/**; backend/app/api/{gmail,ai_secretary,project_contacts}.py; backend/tests/test_v54_context_communication.py | Source resolver + Task claim интерфейс; hypotheses/CAS/ingress checkpoint; НЕ Task/approval writer |
| Task domain | backend/app/task_claims.py; backend/app/task_engine.py; backend/app/api/{tasks,responses}.py; backend/tests/test_v54_task_domain.py | Schema; DeadlineClaim single writer и DB-only create/cancel; no hidden commit/Obligation/publish, legacy regression |
| Trust/Audit | backend/app/action_trust/**; backend/app/audit_writer.py; backend/tests/test_v54_action_trust.py | Source/Context/Task/permission contracts; ledger единственный writer, T1/T2 и frozen approval; fake domain до integration |

Source/Context/Trust могут писать чистые contract-tested facade с согласованными
stubs параллельно; реальное соединение только последовательно после schema,
Identity→Source→Claim/Context→Trust. Stub PASS не runtime PASS.

После объединения этих передач единственный **backend wiring интегратор** меняет
backend/app/main.py, backend/app/jobs/handlers.py и backend/app/jobs/scheduler.py;
остальные существующие backend/app/jobs/** только если отдельный
queue review доказал необходимость. Он подключает ID-only jobs и recovery
pending_dispatch; другие исполнители не трогают handlers.py. Workspace no-copy
cutover backend/app/api/workspace.py отдельное задание владельцу storage,
не параллельная скрытая правка в Source-потоке.

Frontend получает ObjectRef/VersionPin, effective evidence status без цитат по
умолчанию, CAS conflict, frozen payload/hash, отдельные context/claim/approval
кнопки, honest UNKNOWN/cancel. Его область frontend/** и browser tests,
после API snapshot; не создавать ещё один approval store. В этом задании UI нет.

Handoff каждого: точный base/SHA, список файлов, schema requests без миграций,
positive+negative tests собственного контракта, permissions/retention checks,
указание stub/runtime различий, no secrets/content logs. Общий интегратор
затем запускает полный regression + INT-01…23 (AUTO только deny до O1),
PostgreSQL barriers/crash, safe artifacts и clean diff. Только фактический
протокол может закрыть соответствующий runtime gate.
