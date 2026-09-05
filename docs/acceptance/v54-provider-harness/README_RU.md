# V5.4: синтетический provider acceptance harness

## Назначение

Harness фиксирует минимальный контракт будущего адаптера между Communication-to-Action и эффектом провайдера. Он полностью синтетический: не подключает Gmail, Telegram, Google Tasks, реальные почтовые ящики, очереди или production-данные. Реализация находится только в тестовой поддержке и не является вторым action engine.

Статус доказательства:

- **HARNESS CONTRACT PASS** — поведение fake provider и тестового facade проверяется контрактными тестами;
- **PRODUCT INTEGRATION NOT RUN** — реальная композиция PU Workspace к этому Protocol пока не подключена;
- **LIVE PROVIDER NOT RUN** — сетевые вызовы и реальные учётные записи намеренно отсутствуют.

## Модель

`CommunicationActionPort` — минимальный тестовый Protocol. `SyntheticCommunicationActionHarness` моделирует ASSIST/CONFIRM/AUTO, точное approval, проверку области доступа и reconciliation. `StrictFakeProvider` моделирует только provider effect.

Fake provider хранит:

- хэш точной mailbox identity (`provider + account_id + namespace`);
- generation учётных данных и версию capability snapshot;
- opaque action/command IDs и SHA-256 payload;
- безопасные счётчики, outcome и scoped provider external ID.

Он не хранит тело письма, адрес получателя, email, токен, вложение, документ или raw payload. Одинаковый `provider external ID` допустим в разных mailbox и разрешается только вместе с точной mailbox identity.

## Проверяемые правила

| Сценарий | Контракт |
|---|---|
| A | Message/attachment представлены opaque evidence pins; подтверждённое действие создаёт internal Task |
| B | Повтор delivery/command возвращает прежний receipt и не создаёт второй эффект |
| C | Исправление project/contract создаёт новую context revision и сохраняет историю |
| D | ASSIST не вызывает effect; AUTO запрещён; high-risk CONFIRM без approval блокируется |
| E | Изменённые revision/payload требуют нового approval; переиспользование command key с другим payload конфликтует |
| F | Timeout после эффекта даёт UNKNOWN; слепого повтора нет, нужен scoped lookup/reconciliation |
| G | Corrective follow-up — отдельное действие с отдельными command key, approval, receipt и audit |
| H | Mailbox не объединяются по одинаковым provider object/thread IDs |

Дополнительно проверяются timeout до эффекта и явный безопасный retry, stale authority/capability/credential generation, project/mailbox mismatch до provider call, reversible/compensatable/irreversible семантика и отсутствие чувствительных значений в журнале.

## Запуск

Из каталога `backend`:

```powershell
python -m pytest tests/test_v54_provider_acceptance_contract.py -q
```

Тесты используют только синтетические значения и не требуют сети или секретов.

## Точный interface request интегратору

Нужен один adapter к существующей продуктовой композиции, реализующий семантику `CommunicationActionPort`, без копирования harness в runtime:

1. На входе принимать immutable action envelope: stable `action_id`, `revision`, canonical `payload_hash`, `command_key`, mode/risk/reversibility, точную mailbox identity, project/context/evidence pins, authority epoch, credential generation и capability snapshot version.
2. Approval привязывать одновременно к action/revision/hash, mailbox, project, capability version, credential generation и authority epoch.
3. Непосредственно перед effect повторно проверять live mailbox/project authority, capability и credential generation.
4. В provider adapter передавать только минимальные provider-поля; idempotency связывать с `(exact_mailbox_identity, command_key, payload_hash)`.
5. Возвращать единый business receipt с `APPLIED`, `NOT_APPLIED` или `UNKNOWN`; queue lease/attempt не считать business outcome.
6. Для `UNKNOWN` предоставить scoped lookup/reconciliation по exact mailbox и command/provider external ID; автоматический повтор до reconciliation запрещён.
7. Corrective/compensating operation создавать как новое действие с новым approval/audit. Для irreversible send не публиковать `ROLLED_BACK`.
8. Audit и технические логи должны принимать только opaque IDs, hashes, safe outcome/error code и counters — без body, recipient, email, token, attachment или raw payload.

Для продуктового PASS интегратору отдельно нужны транзакционная связка action/approval/outbox/receipt, durable recovery, конкурентные тесты с PostgreSQL и adapter contract tests реального провайдера. Они намеренно не входят в этот harness.
