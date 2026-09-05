# MVP1 storage mutation acceptance

Дата: 2026-09-05

База: `855a30df54ed7477c8180ad1c5ed920584f94656`

Ветка: `codex/mvp1-storage-mutation-acceptance`

## Результат

Добавлен provider-neutral fail-closed контракт для rename/move/rollback,
предназначенный для вызова существующим durable job handler. Новая очередь,
production wiring и реальные provider-вызовы не добавлялись.

Каждая команда фиксирует exact `project_id`, `provider`, `connection_id`,
`folder_id`, binding version, CAS record version и для каждого объекта source
revision, исходные name/parent и целевые name/parent. Вложенные folder/object IDs
считаются opaque и не преобразуются в корневой путь.

До первой мутации выполняется dry-run всех source pins. Повтор одинаковой команды
возвращает тот же immutable receipt; reuse ключа с другим payload запрещён.
При provider failure уже выполненные операции компенсируются в обратном порядке.
Receipt различает `applied`, `compensated`, `partial_failure`, `rolled_back`.
Rollback является отдельной CAS/idempotent командой и не изменяет исходный receipt.

## Покрытие

- Google Drive и Яндекс Диск через одинаковый synthetic adapter contract;
- nested folder rename + move;
- rollback;
- stale project/provider/connection/folder/binding/version;
- stale source revision и изменённые name/parent;
- идемпотентный replay и конфликт payload;
- полная компенсация и частичная ошибка компенсации;
- immutable frozen receipt;
- отсутствие возврата к binding старого проекта.

Проверки: 4 synthetic сценария (включая параметризацию двух providers) PASS через
прямой test invocation; Python compile и `git diff --check` PASS. Полный pytest
не запускался из-за отсутствующего локального Python 3.13/native libpq после
перезагрузки среды.

## Live gate

Статус `CONDITIONAL`: реальные OAuth accounts, provider latency/rate limits,
revision semantics Google/Яндекс, crash между provider effect и durable receipt,
PostgreSQL CAS и worker lease recovery должны проверяться отдельно в изолированном
CI. До этого модуль не следует подключать к production handler.

Для production wiring нужен durable `MutationReceiptStore` поверх существующих
organizer operation tables и адаптеры `object_revision()`; содержимое файла,
OAuth token и абсолютный путь не должны попадать в job payload или receipt.
