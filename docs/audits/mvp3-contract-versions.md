# MVP3 M3-08 — версии и безопасный lifecycle договоров

Дата проверки: 2026-09-05

Ветка: `codex/mvp3-contract-versions`

База: `e8801c66632b10e158bbea66de54eca1d273b78f`

## Найденный дефект

Поле `Contract.record_version` существовало, но PATCH его не проверял и не увеличивал. Истории карточки договора не было. Физическое удаление разрешалось любому договору без обнаруженных зависимостей, поэтому содержательная карточка могла исчезнуть без immutable snapshot.

Regression-тест до реализации завершался ошибкой импорта отсутствующего `ContractVersion`.

## Реализовано

- immutable `ContractVersion` со снимком бизнес-полей и привязанных документов;
- снимки `created`, `baseline`, `updated`, `linked`, `analyzed`, `archived`, `deleted`;
- CAS `expected_record_version` для PATCH, DELETE и привязки пакета документов;
- повтор команды с актуальной версией и без фактических изменений не создаёт новую версию;
- stale mutation возвращает HTTP 409 и не перезаписывает карточку;
- отдельный read endpoint `/projects/{project_id}/contracts/{contract_id}/versions`;
- история и текущая `record_version` включены в карточку договора;
- физическое удаление разрешено только ошибочному пустому draft без зависимостей;
- активные, архивные, связанные и содержательные договоры можно только архивировать;
- история удалённого пустого draft сохраняется намеренно без FK на `contracts`;
- исходные документы и evidence не удаляются;
- UI передаёт CAS-версию и показывает номер версии/число снимков.

## Схема

Новая последовательная миграция: `a54f001c0a11` после `a54f001c0a10`. Alembic head одна. Обновлены schema/readiness/runtime pins.

## Проверки

- regression/contract/migration/package: 28 passed;
- первый полный backend: 1191 passed, 19 skipped;
- финальный полный backend после baseline-hardening: 1192 passed, 19 skipped и один transient performance failure (`12.68s < 10s` не выполнено при параллельной нагрузке); изолированный повтор performance-теста: 1 passed за 2.86s;
- scripts/ci contract: 19 passed;
- frontend Vitest: 102 passed;
- TypeScript check: PASS;
- frontend production build: PASS;
- `git diff --check`: PASS.

Пропуски полного backend-набора относятся к уже условным PostgreSQL/provider сценариям. В доступном окружении отдельный `TEST_POSTGRES_DSN` не предоставлен; реальное применение `a11` и конкурентный CAS на PostgreSQL остаются обязательной внешней runtime-проверкой.

## Ограничения

- История фиксирует состояние карточки и идентификаторы связанных документов, но не копирует содержимое документов.
- Для договоров, существовавших до `a11`, первый post-migration mutation сначала создаёт `baseline` snapshot.
- Удаление проекта каскадно удаляет его историю договоров как часть полного tenant/project lifecycle; отдельное физическое удаление договора историю не удаляет.
- Production, DNS и production DB не изменялись из-за EU cutover freeze.
