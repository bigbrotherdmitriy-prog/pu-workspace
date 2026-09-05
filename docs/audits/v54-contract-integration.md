# v5.4 contract integration — результат

Дата: 2026-09-03. Статус: **NEEDS DECISIONS**. Только документация и synthetic
fixtures; не PRODUCT PASS и не подтверждение реализации MVP5.

## Исходное состояние и перенос

База `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`, отдельная worktree
`pu-workspace-v54-contract-integration`, ветка `codex/v54-contract-integration`.
При создании одноимённые branch/worktree отсутствовали; начальный status чистый.
Применимых AGENTS.md в дереве/проверенных родительских каталогах не обнаружено.
Наличие трёх commits и merge-base каждого с базой проверены: ровно указанная база.

Основная worktree оставлена на `codex/commercial-p2-yandex360`, HEAD
`83774aac726acd4e27b349e9194f30783158bde8`. Её незакоммиченные файлы:
backend/app/api/auth.py, backend/app/api/local_upload.py, backend/app/api/workspace.py,
backend/app/schema.py, backend/app/static/app.js, docker-compose.yml, frontend/index.html.
Они не копировались, не редактировались и не коммитились этим потоком.

| Порядок | Исходный commit | Cherry-pick в этой ветке |
|---|---|---|
| 1 Source/Evidence | 606dc4866fdeb376c5cdcfda2ed75160fecea553 | 491a7ac1a5a627314ca1530b34970bfda3507bd7 |
| 2 Context/Communication | ba71089c64d51ec2cf9d2f5f5fb834a53c95081b | b27d82d7e07053b5b46f952e28792a3b6c24c857 |
| 3 Action Trust | 7d3a8c1a542a17793c09bf38197ab6c889aabf5e | 7b258fbd519405c458203d0c51dcc73793757e95 |
| 4 Integration | Отдельный docs commit после трёх выше | Полный SHA в итоговом ответе / git log |

Cherry-pick без текстовых конфликтов; семантические пересечения перечислены в
[ADR](../architecture/v54/integration/decisions.md). Runtime 531bd25 и staging fork
не переносились. Исторические audits трёх пакетов не редактировались.

ТЗ `C:/Users/dpush/Downloads/PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`
прочитано через OOXML с использованием documents skill и bundled Python:
содержательные требования/таблицы, не проверка Word-вёрстки. Исходник не менялся.
Титул v5.1 и дополнения v5.4 различены; нет выдуманных номеров страниц.
Опорные пункты: §8 IDs, §19 audit, §31 Context→Action→Human Control и Strategic
Trust §§1–9, §35–36 quality/API, scope freeze. AUTO §9B намеренно остаётся открытым.

## Что согласовано

Один [integration пакет](../architecture/v54/integration/README.md):
ObjectRef/VersionPin с explicit int/uuid и tenant; semantic pins вместо mutable
freshness версии; Task-owned DeadlineClaim; единый ConnectionIdentity и mailbox
extension; Trust-owned DB transaction с Task/receipt/ledger; pending-dispatch
через существующую очередь; один audit writer и контролируемый retention.

Source/Context/Action README ссылаются на общий приоритет. Убраны конкурирующие
определения claim owner, wire shorthand и account master из исходных contracts.
Исходные standalone JSON сохранены без подмены их hashes; они не integrated API.
Fake send/reply/escalation/AUTO сценарии явно отложены. Новая fixture — только
синтетические message+attachment, date-only claim, human context/claim review,
два CONFIRM действия create/cancel и неотправленный draft.

План [миграций/следующей волны](../architecture/v54/integration/migration-handoff.md):
решения→identity/tenant→sources/evidence/claim/context→trust/audit→shadow→CONFIRM.
Один schema owner и одна head, feature flags OFF, legacy protection, single writers,
без массового backfill, rollback поведения без удаления истории. Параллельные
границы файлов указаны для Identity, Source, Context, Task и Trust; handlers.py/
main.py и все migrations только у интегратора после передачи интерфейсов.

## Проверки и пределы

Команда: `python docs/architecture/v54/integration/validate.py` из worktree.
Проверяет JSON syntax/duplicate keys, ObjectRef ID/type/tenant, ссылки и version
pins, два canonical action hash и policy hash, approvals/receipts/ledger sequence,
synthetic task cancellation, ID-only job payload, четыре намеренные подмены
примеров, восемь исходных Action hash, локальные Markdown targets/anchors.
Дополнительно `git diff --check` и file allowlist; результаты зафиксированы ниже.

Фактический локальный результат: `DOCUMENT_CHECKS_PASS records=37 actions=2
mutation_checks=4 local_links=68 legacy_hashes=8`, exit 0. `git diff --check` —
exit 0; только предупреждения Git LF→CRLF, без whitespace errors. Проверяется
37 records одной fixture; число не является числом runtime tests.

Runtime/PostgreSQL/browser/provider тесты НЕ выполнялись и не входят в docs-only
задачу. INT-01…23 — acceptance specification, не passed тесты. Ни synthetic
receipt, ни hash-validator не доказывают exactly-once provider execution.
Существенные оставшиеся gaps: DB-only helpers, authority epoch writers, mailbox
origin/ACL, version-aware source verification, audit transaction facade,
existing snapshot auto-copy gate и отдельный runtime CI. Staging не реализуется
этим пакетом. Внешние эффекты первого среза исключены.

## Решения владельца

O1 — narrow AUTO/квоты/expiry; O2 — access/freshness/retention/residency;
O3 — реальные роли reviewer/approver и self-approval; O4 — account subject/reconcile;
O5 — transaction/epoch interfaces и physical audit design; O6 — редакция ТЗ и
time-specific deadlines. Подробнее ADR. До решений default deny; синтетические
политики не являются разрешением реальному tenant. Финансовые подтверждения
независимы, пользовательская оплата не требует обязательной банковской выписки.

Backend/frontend/jobs/OAuth/legal/product migrations/secrets не изменялись.
Production, push, PR, merge и deploy не выполнялись.
