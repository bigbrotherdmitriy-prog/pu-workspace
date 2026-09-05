# MVP1: provider mutation primitives audit

Дата проверки: 2026-09-05  
База: `1500bad`  
Ветка: `codex/mvp1-storage-provider-primitives`

## Результат

Статус: **HARD DENY для Google Drive v3 и Яндекс Диск REST**.

В публичных официальных контрактах обоих используемых API не найдено
поддерживаемой атомарной precondition для rename/move по exact revision/ETag.
Поэтому низкоуровневые mutating methods не добавлялись: read-before-write или
недокументированный HTTP-заголовок не считаются доказательством безопасности.

В существующих клиентах закреплены явные capability markers со значением
`False`. Интеграционный wrapper останавливается до построения/отправки HTTP
запроса даже при ошибочной попытке включить его через `enabled=True`.

## Доказательства из официальной документации

### Google Drive API v3

1. [`files.update`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update)
   документирует PATCH, `fileId`, `addParents`, `removeParents` и остальные query
   parameters, но не документирует `If-Match`, ETag или expected `version`.
2. [`File.version`](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
   является output-only монотонным номером версии. Он полезен для обнаружения
   изменений, но не передаётся в `files.update` как atomic precondition.
3. [Сравнение Drive API v2 и v3](https://developers.google.com/workspace/drive/api/guides/v2-to-v3-reference)
   прямо отображает поле `Files.etag` из v2 в `n/a` для v3.

Вывод: текущий Drive v3 client не может доказать, что rename/move применён именно
к прочитанной версии объекта. Подстановка `If-Match` на основании не
документированного v3 ETag запрещена.

### Яндекс Диск REST API

1. [Официальное введение и перечень операций](https://yandex.ru/dev/disk-api/doc/ru/)
   ведёт на move и metadata endpoints REST API.
2. [Перемещение ресурса](https://yandex.ru/dev/disk-api/doc/ru/reference/move)
   описывает `from`, `path`, `overwrite`, `force_async` и response operation, но
   не exact revision/ETag/`If-Match` precondition.
3. [Метаинформация ресурса](https://yandex.ru/dev/disk-api/doc/ru/reference/meta)
   возвращает сведения ресурса, но read metadata отдельно от POST move не делает
   пару операций атомарной.

Вывод: проверка path/md5/modified перед `resources/move` оставляет TOCTOU. Она не
может использоваться для live enablement.

## Изменения

- `DriveClient.supports_exact_mutation_preconditions = False` и стабильный
  blocker code;
- `YandexDiskStorageAdapter.supports_exact_mutation_preconditions = False` и
  стабильный blocker code;
- HTTP-mock regressions доказывают отсутствие обращения к Google service и
  Яндекс transport при default deny и при ошибочном `enabled=True`;
- OAuth, токены, factory, feature flags, durable queue и production wiring не
  изменялись.

## Проверки

Из каталога `backend`:

```powershell
python -m pytest tests/test_mvp1_storage_provider_primitives.py tests/test_mvp1_storage_live_adapters.py -q
```

Результат: `23 passed`.

Полный storage-mutation acceptance/API/repository/runtime/wiring набор:
`47 passed, 3 PostgreSQL-only skipped` (без `TEST_POSTGRES_DSN`).

Ни один реальный API-вызов не выполнялся. Тестовый Google service и `httpx`
transport настроены как fail-on-call; счётчик обращений остался равен нулю.

## Точный live-test gate

Для снятия hard deny нужен один из следующих подтверждённых вариантов для
каждого провайдера:

1. официальный rename/move endpoint с атомарным expected revision/ETag;
2. официально документированный `If-Match` именно для используемого resource и
   версии API;
3. отдельный server-side transaction/lock primitive, связывающий exact read и
   mutation.

После появления primitive необходимы HTTP-contract тесты на 412/409, race между
read и update, timeout before/after, delayed effect, deterministic operation key,
reconciliation и no-double-effect, затем изолированный live OAuth cohort.

До этого момента Google/Яндекс rename/move могут оставаться только в synthetic
acceptance. Live activation через feature flag, UI или worker запрещена.

## Ограничения

- исследовалась текущая публичная документация на дату отчёта; изменение API
  провайдером требует повторного аудита;
- undocumented server behaviour не тестировалось и не считается контрактом;
- live latency/rate limits/OAuth и PostgreSQL concurrency не проверялись.
