# PU Workspace: аудит архитектуры MVP4 → MVP5 Pilot Ready

Дата: 30.08.2026. База: ТЗ v5.2 Provider Agnostic.

## Итог

Текущая архитектура не требует переписывания Core для MVP5. Provider-specific код уже отделён контрактами адаптеров, а основной Communication-to-Action работает через предметные сущности Core. Для Pilot Ready важнее стабилизация сквозного сценария, наблюдаемость и приёмочные тесты, а не добавление новых модулей.

## Матрица требований

| Требование | Уже поддерживается | Частично / не поддерживается | Изменить сейчас | Можно отложить | Риск отсрочки |
|---|---|---|---|---|---|
| Provider-agnostic Core | `IntegrationAdapter`, `StorageAdapter`, `ChannelAdapter`, `AIProviderAdapter`, registry; Google, Telegram и Gemini находятся в реализациях | Единственная реализация ActionAdapter сейчас Google Workspace | Нет | Новые providers | Низкий: Core уже отделён |
| Communication-to-Action | Gmail/Telegram → sender/project/contract → document/task/risk/decision/draft; approval; external publish; dashboard | Нет единого формального state-machine объекта workflow | Проверить сквозными тестами и пилотным сценарием | Универсальный workflow engine | Средний, если новые каналы начнут копировать логику |
| Project Context | Project, Contract, Document, DocumentVersion, Message, Task, Decision, Payment и Person/Company связаны FK; сохраняются confidence/evidence/source | Нет универсального relation edge и Clause/RFI/Change как отдельных сущностей | Нет | Типизированный relation edge и новые узлы | Низкий до появления второго сложного вертикального сценария |
| Approval и Audit | Предложения отделены от исполнения; Gmail send требует approved; внешние задачи требуют manager approval; финансы подтверждает manager; audit log существует | Organizer audit не указывал actor в части событий | Добавить actor в существующие audit details | Нормализованные policy/action tables | Высокий для пилотной ответственности — исправлено |
| AI Secretary → Orchestrator | AI provider отделён; AI policy поддерживает режим без внешнего AI | Нет agent registry и специализированных агентов | Нет | Orchestrator и agents | Низкий: интерфейсы не блокируют развитие |
| Deployment portability | Docker, migrations, env configuration, atomic releases | Нет готовых пакетов для каждого российского облака/on-prem | Нет | Deployment profiles | Средний только при подписанном on-prem пилоте |
| Project Lifecycle | Текущий Core покрывает исполнение, документы, договоры, задачи и финансы | Handover/Warranty/Operation не реализованы | Нет | После 1.0 | Низкий для MVP5 |

## Классификация

### A — MUST HAVE для MVP4

- Сохранить работающие Projects, Documents, Gmail, Tasks и AI Secretary.
- Не допускать EXECUTE без role check, approval и audit.
- Указывать actor в audit trail критических действий Organizer.
- Поддерживать безопасную копию и неизменность оригиналов по умолчанию.
- Держать полный backend regression suite зелёным.

### B — MUST HAVE для MVP5 Pilot Ready

- Один приёмочный Communication-to-Action сценарий на реальных данных пилота.
- Проверка маршрутизации sender → project → contract с ручным подтверждением низкой уверенности.
- Контроль proposed → approved → executed для задачи и ответа.
- Просрочка в dashboard/notifications и ежедневной сводке без автоматического внешнего действия.
- Runbook подключения организации, резервного копирования, восстановления и диагностики adapters.

### C — Architecture Only

- Типизированный `ContextRelation` с source/target/relation_type/confidence/evidence/created_by/audit metadata.
- Agent/skill registry для будущего AI Orchestrator.
- Provider-neutral workflow state machine.
- Deployment profiles для cloud/private/on-prem.

Интерфейсы C не следует реализовывать до появления второго реального consumer или пилотного требования.

### D — Roadmap после 1.0

- Preconstruction → Construction → Handover → Warranty → Operation.
- Schedule, Finance/DDS, Drawing/BIM и Field Agents.
- RFI, Clause, Change и полная contract-risk graph semantics.

## Следующий конкретный шаг к MVP5

Зафиксировать один pilot acceptance test: входящее письмо реального формата → подтверждение проекта и договора → предложение задачи и срока → approval → публикация → просрочка/закрытие → ежедневная сводка. Не добавлять второй provider до прохождения этого сценария.
