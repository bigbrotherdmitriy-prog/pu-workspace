# MVP4 evidence ingestion bridge

Дата проверки: 2026-09-05

Ветка: `codex/mvp4-evidence-ingestion-bridge`

База: `a29432d5eb8915441e29a49a24a5717738ef6d65`

## Результат

Blocker M4-02 закрыт для синтетического и локального test scope: legacy-индексация
создаёт отдельную immutable `SourceVersion`, точно связанную с текущей
`DocumentVersion` через `legacy_document_version_id`. Юридические и финансовые
потребители больше не выбирают `latest` наугад. Если connection или provider
revision отсутствуют, связь не создаётся и существующий `persist_contract_evidence`
возвращает `manual_review_required / exact_source_version_unavailable`.

Production, DNS, production DB и provider API не изменялись. Deploy, push и merge
не выполнялись.

## Аудит ingestion paths

| Путь | До изменения | После изменения |
| --- | --- | --- |
| Google Drive metadata snapshot + analysis | `DocumentVersion` без SourceVersion | точная digest-observed версия при наличии connection и checksum/modified time |
| Яндекс Диск metadata snapshot + analysis | то же | тот же provider-agnostic контракт |
| Safe copy + standardization | индексировалась только legacy-копия | copy object получает собственную source identity и exact version; оригинал не меняется |
| OCR reindex | обновлял legacy DocumentVersion | bridge применяется, но несовпадение активного provider или отсутствие revision fail-closed |
| Local upload encrypted staging | SourceVersion существовала до DocumentVersion, точной связи не было | exact staging version передаётся внутренним аргументом; создаётся отдельный derived document source |
| Gmail attachment encrypted staging | metadata-only SourceVersion нельзя было связать с извлечённым текстом | создаётся отдельный digest-observed child source; origin current staging не сдвигается |
| Manual history snapshot | локальная пользовательская версия без provider observation | намеренно не связывается автоматически |
| Telegram synthetic document | не является Drive/Yandex/staging path | намеренно остаётся вне этого bridge |

## Инварианты

- В SourceVersion не сохраняются текст документа, provider path, URL, токен или
  исходный абсолютный путь.
- Locator содержит только внутренние integer IDs, provider и имя pipeline.
- Provider object ID и account signal хешируются перед сохранением source identity.
- SourceVersion создаётся детерминированно из source identity, provider revision и
  SHA-256 точного `DocumentVersion.content`.
- Повтор после рестарта сходится к существующим SourceReference/SourceVersion.
- Изменившееся содержимое создаёт новую immutable SourceVersion и переносит только
  `SourceCurrent` derived document source.
- Для local upload/Gmail создаётся child SourceReference. `SourceCurrent` исходного
  staging source остаётся на исходной версии, поэтому retention/cleanup/recovery не
  ломаются.
- Tenant и project проверяются до привязки; exact parent обязан быть current,
  available и иметь policy/residency metadata.
- Job payload не изменён. Содержимое, provider locator и source IDs в него не добавлены.
- Новая очередь, registry, ledger или миграция не создавались.

## Regression-first

До реализации:

- 4 теста Google Drive/Яндекс Диск падали из-за отсутствующей exact SourceVersion;
- 2 fail-closed теста проходили и подтверждали безопасный manual review.

После реализации:

- targeted: `31 passed`;
- полный backend: `1154 passed, 19 skipped`;
- Alembic: единственная head `a54f001c0a09`;
- `CURRENT_SCHEMA_REVISION = a54f001c0a09`;
- `git diff --check`: PASS.

Команды:

```powershell
python -m pytest tests/test_v54_legacy_ingestion_bridge.py `
  tests/test_v54_local_upload_a05_wiring.py `
  tests/test_v54_gmail_a05_wiring.py `
  tests/test_contract_financial_evidence.py `
  tests/test_document_engine.py `
  tests/test_storage_provider_regression.py -q --tb=short `
  --basetemp=.pytest-mvp4-bridge-targeted3

python -m pytest -q --tb=short --basetemp=.pytest-mvp4-bridge-full
python -m alembic -c alembic.ini heads
git diff --check
```

## Ограничения и последующая проверка

- PostgreSQL concurrency не запускалась в этой локальной среде. Детерминированные
  IDs и row locks подготовлены, но concurrent first-insert следует проверить в CI.
- Live Google Drive/Яндекс Диск не вызывались; нужны отдельные test accounts и
  обезличенные файлы для проверки реальных revision metadata.
- Google native files без checksum допускаются только при наличии `modifiedTime`.
  Если adapter не даёт ни checksum, ни modifiedTime, результат остаётся manual review.
- Bridge создаёт exact source/version, но не создаёт representation materialization;
  чтение fragment остаётся fail-closed до отдельного разрешённого materialization flow.
- Manual history snapshots и Telegram требуют отдельного evidence policy, поэтому
  автоматический fallback на них не добавлялся.
