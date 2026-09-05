# ContextRelation и Communication-to-Action — контракт одного MVP5-пилота

Стыки общего пилота уточняет [integration contract](../integration/README.md).
Общие ID/версии/owners определены там. Исходные standalone примеры сохранены
для истории design proposal; AUTO/send/reply/escalation не входят в первый срез.
Единственный интегрированный wire-пример — [pilot.json](../integration/pilot.json).

Статус: **design proposal / требуется согласование интегратором**, не реализованный API.
База кода: `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.
Версия контракта: `v54-context-communication/1`.

Пилот: синтетическое письмо с одним вложением → проект/договор → evidence
срока → предложение внутренней задачи и черновика → подтверждение конкретной
версии → общий action contract → существующий Task/ResponseDraft и общий аудит.
Реальная отправка не входит в эту документационную задачу. Будущая приёмка
исполнения send использует fake Gmail transport без внешней сети.

Документы пакета:

- [Контракт связей и обработки](contract.md).
- [Migration proposal и решения интегратора](migration-proposal.md).
- [Пилот, ошибки и acceptance](pilot-acceptance.md).
- [Синтетические примеры](examples.json).
- [Аудит базы и соответствие ТЗ](../../../audits/v54-context-communication-contract.md).

## Источник требований и разрешение неоднозначностей

Прочитан DOCX `PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`, SHA-256
`AF7BFDE75715345E4F32B9D7CA057812CDBA7B8D8E0B6A1B105DFE20FC0D5DF3`.
В документе сохранены титул «Версия 5.1» и исторический freeze первого среза;
раздел 31 содержит дополнения Context → Action → Human Control и v5.4
Federated Evidence Autonomy. Для этого пакета применяются эти дополнения и
явный запрос пользователя на проектирование одного MVP5-пилота. Это не
разрешение реализовать весь Product Scope или переписать текущий Gmail.

Релевантные требования: §3, §6, §7–8, §15–16, §19–22, §25, §31 (семь
стратегических принципов, MVP5 сценарии), §35–36. Нумерация страниц не используется:
чтение выполнено по OOXML в порядке абзацев и таблиц, вёрстка DOCX не оценивалась.
Исходный файл не менялся; выход пакета — Markdown/JSON.

## Границы владения

| Область | Владелец | Что потребляет этот контракт |
|---|---|---|
| ContextRelation, message identity, resolution/analysis orchestration | Этот поток | Контракты ниже; реализация отдельным заданием |
| SourceReference / Evidence, locator, source version, retention/residency | Поток Evidence | Непрозрачные versioned ссылки, resolve/validate/access результат |
| Proposal/Approval/Policy/Execution/Action Ledger | Общий action-поток | Intent, immutable payload reference/version, решение и execution receipt |
| BackgroundJob, workers, leases, retries, cancellation | Существующая очередь | enqueue/claim/heartbeat/fencing; никаких новых брокеров |
| Gmail read/MIME/send, Task и ResponseDraft | Существующие обработчики | Минимальная обёртка/facade после согласования контракта |

В examples.json `source_ref`, `evidence_refs`, `proposal_ref`, `approval_ref`,
`execution_ref`, `ledger_event_ref` — **ссылки владельцев**, не определения их
таблиц/схем. Названия интерфейсных операций ниже логические, URL не существуют.
Включение пилота блокируется до согласования этих границ; не создавать временный
второй approval/ledger для демонстрации.

Не входят: корпоративный чат, graph database, новые провайдеры, автономные агенты,
универсальный rule-learning, финансовое исполнение, перенос документов в PU,
новый encrypted staging и расширение очереди вложениями.

Критерий готовности **этого пакета** — согласуемый дизайн с примерами и
проверяемыми сценариями. Критерий **MVP5 Pilot Ready** — реализация и фактический
проход этих сценариев на PostgreSQL, с общими Evidence/Action контрактами.
