# MVP-2 — замена regex на LLM-извлечение: отчёт о реализации

Дата: 2026-09-12

Ветка: `codex/mvp2-audit`. Реализация выполнена по плану, утверждённому
пользователем поверх [`mvp2-ai-extraction-preflight.md`](mvp2-ai-extraction-preflight.md)
(развилки §8-11 решены пользователем в переписке, зафиксированы там же).
**Код не закоммичен** — изменения застейджены (`git add -A`), ждут
подтверждения перед commit.

## 1. Что реализовано (файл за файлом, в порядке из плана)

| # | Файл | Изменение |
|---|---|---|
| 1 | `backend/migrations/versions/d29a6c4f1e83_add_llm_task_extraction_fields.py` (новый) | 7 колонок на `tasks` и `obligations`: `amount`, `amount_currency`, `amount_evidence_quote`, `due_date_evidence_quote`, `assignee_hint`, `assignee_evidence_quote`, `extraction_method` (default `'regex'`). `down_revision = c13606d92787` (текущая единственная голова) |
| 2 | `backend/app/models/task.py`, `backend/app/models/management.py` | те же 7 колонок на ORM-моделях `Task`/`Obligation` |
| 3 | `backend/app/gemini_analysis.py` | `COMBINED_EXTRACTION_SCHEMA` (obligations+response_candidates+risks+decisions одной схемой), `COMBINED_EXTRACTION_SYSTEM_INSTRUCTION`, `extract_combined_fields_with_gemini()` |
| 4 | `backend/app/integrations/ai.py` | `GeminiAIAdapter.extract_fields(...)` — новый метод провайдер-нейтрального адаптера |
| 5 | `backend/app/document_extraction.py` (новый, 433 строки) | Общая точка входа для всех трёх движков: regex-предфильтр, классификация 4 причин отказа LLM, fallback на regex (Вариант А), верификация evidence-цитат подстрокой, confidence enum→float, matching `assignee_hint`→реальный участник, ограниченный пул воркеров (Вариант Б), transient-кэш на файл |
| 6 | `backend/app/task_engine.py` | `create_tasks_from_files` — потребляет `document_extraction`; `extract_task_candidates`/regex — **не тронуты**, тело fallback |
| 7 | `backend/app/response_engine.py` | `create_response_drafts` — аналогично; `extract_response_candidates` — не тронут (только `ensure_response`-хвост вынесен в переиспользуемую `ensure_response_bonus()`, поведение идентично) |
| 8 | `backend/app/governance_engine.py` | `create_governance_items` — аналогично; `extract_governance_candidates`/regex — не тронуты |
| 9 | `backend/app/summary_engine.py` | **не тронут** (решено пользователем — вне scope) |
| 10 | 9 вызывающих мест (`organizer.py`, `ocr_batch.py`, `api/workspace.py`, `local_upload_staging.py`, `staging/gmail_a05.py`, `api/contract_package.py`, `api/management.py`, `api/organizations_contracts.py`, `api/telegram.py`, `api/ai_secretary.py`) | **не тронуты вообще** — подтверждено `git diff --stat`, ни один не входит в список изменённых файлов |

**Побочные, но необходимые правки** (не входили в исходный список файлов,
но без них план не работал технически):
- `backend/app/ai_policy.py` — extract-method рефакторинг:
  `prepare_external_ai_text` разбит на `policy_mode_for_project` (db) +
  `apply_ai_policy_mode` (чистая функция) — нужно, чтобы пул воркеров не
  трогал `db` из чужого потока. Публичное поведение `prepare_external_ai_text`
  не изменилось (проверено существующими тестами `test_ai_policy.py`,
  `test_mail_client_api.py` — 18/18 прошли без изменений).
- `backend/app/core/integration_types.py` — добавлено одно поле
  `llm_extraction_cache` на `StorageObject` (transient, `compare=False`,
  `repr=False`) — механизм кэша "один LLM-вызов на файл на все три
  движка", описанный в плане.
- `backend/app/schema.py` — `CURRENT_SCHEMA_REVISION` обновлён на новую
  миграцию.
- **10 файлов с захардкоженным ID предыдущей головы миграции** — найдены
  через `grep -rl "c13606d92787"` по всему репозиторию (не только
  `backend/`): `.github/workflows/docker-smoke.yml`,
  `scripts/ci/durable_queue/run.py`, `scripts/ci/v54_pilot_workflow.py`,
  `scripts/ci/test_v54_pilot_workflow.py`, `scripts/ci/test_v54_wave3_ci_gate.py`
  и 5 postgres-gated тестов (`test_mvp1_google_metadata_contract.py`,
  `test_v54_autonomy_authorization.py`, `test_v54_deadline_precision.py`,
  `test_v54_materialization_postgres.py`, `test_v54_pilot_foundation.py`,
  `test_v54_provider_action_migration.py`) — все обновлены на `d29a6c4f1e83`.
  Это не связано с LLM-логикой — чисто следствие смены головы миграции,
  обнаружено только при полном офлайн-прогоне (см. §2).

## 2. Гейт 1 — офлайн-регрессия (полный набор)

```
1580 passed, 1 failed, 25 skipped, 23 warnings in 540.36s (0:09:00)
```

Единственный `FAILED` —
`tests/test_mvp1_google_storage_oauth.py::test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision`
(`AttributeError: '_IncludedRouter' object has no attribute 'path'`) —
**pre-existing, не связан с этой веткой**: ломается из-за несовместимости
теста с установленной версией FastAPI (`0.141.1`, ленивый `include_router()`),
воспроизводится и на чистом `main` без единого изменения из этого пакета.
Уже диагностирован и исправлен отдельно на другой ветке
(`codex/gmail-sync-archived-filter` в соседнем воркспейсе) — в этот пакет
намеренно не включён (другая тема, другой коммит).

Первый прогон (до находки захардкоженных ID головы миграции) дал 7 упавших
— все 7 оказались ровно тем же самым хардкодом головы, не логической
регрессией; после правки — воспроизведено чисто.

## 3. Гейт 2 — PostgreSQL tested-runtime

Мигрция добавляет колонки + пул воркеров — оба основания для этого гейта
присутствуют (по критерию из задания). Прогнано на одноразовом,
изолированном контейнере `postgres:16-alpine` (не на проде, не на staging
— отдельный disposable-инстанс, удалён после прогона):

- **`alembic upgrade head`** — вся цепочка миграций (61+1) применяется на
  реальном Postgres без ошибок, финальная `alembic_version = d29a6c4f1e83`.
- **`alembic downgrade -1` → `upgrade head`** — новые 7 колонок корректно
  удаляются и восстанавливаются на обеих таблицах.
- **Реальные типы в БД проверены `\d`**: `numeric(14,2)`,
  `character varying(8/20/300)`, `text`, `extraction_method` —
  `NOT NULL DEFAULT 'regex'::character varying` — как и задумано.
- **Новый postgres-тест** `tests/test_document_extraction_postgres.py`
  (3 теста, конвенция репозитория — выделенная env-переменная
  `PUW_LLM_EXTRACTION_DATABASE_URL`, `pytest.skip` если не задана):
  - round-trip колонок (`amount`/`assignee_hint`/`extraction_method`) на
    реальной записи/чтении;
  - **пул воркеров реально конкурентен** (ровно 3 одновременных вызова
    из 9 файлов при `LLM_EXTRACTION_CONCURRENCY=3`, не 1 и не 9;
    `elapsed < 9×0.1с` — не последовательно) **и при этом ни один
    вызов из фонового потока не совпадает с главным потоком** (проверено
    явно через `threading.get_ident()`) — это и есть гарантия "воркеры не
    трогают `Session`", а не просто утверждение из плана;
  - `create_tasks_from_files` с пулом даёт корректные 6/6 задач с полным
    набором ожидаемых полей.

Все три теста прошли: `3 passed`.

## 4. Гейт 3 — secrets scan

`python scripts/check_ci_security.py` (тот же скрипт, что в
`.github/workflows/security.yml::package-and-secrets`), прогнан после
`git add -A` (сканирует `git ls-files`, значит нужно застейджить новые
файлы, иначе не увидит):

```json
{"passed": true, "findings": []}
```

## 5. Числа по изменению (`git diff --cached --stat`)

- **32 файла** изменено/добавлено, **2774 добавлено / 53 удалено** строк.
- Новых файлов кода: 2 (`document_extraction.py` — 433 строки; миграция —
  48 строк).
- Новых/изменённых тестовых файлов: 8 новых + 7 точечно изменённых
  (только строка с ID головы миграции).
- **42 теста** в затронутых тестовых файлах проходят (из них **38 —
  новые**, добавленные под эту задачу; 4 — существовавшие
  `test_gemini_analysis.py`-тесты, не менялись).
- Существующие regex-тесты, которые по плану не должны были измениться:
  `test_task_engine.py` (8), `test_response_engine.py` (4),
  `test_governance_engine.py` (4), `test_task_text_quality.py` (18) —
  **все прошли без единой правки в самих тестах**, только за счёт того,
  что `extract_task_candidates`/`extract_response_candidates`/
  `extract_governance_candidates` физически не тронуты кодом.

## 6. Не проверено / известные границы этого отчёта

- ~~Реальный вызов Gemini не прогонялся на настоящем `GEMINI_API_KEY`~~ —
  **закрыто, см. §7**: прогнан 2026-09-13 на живом production-ключе,
  найден и исправлен реальный баг схемы.
- §11 (async UX для `confirm_context_bulk`/`analyze_contract_package`) —
  реализован отдельно, после этого отчёта (см. более поздний коммит той
  же ветки).
- `LLM_EXTRACTION_CONCURRENCY` дефолт (4) не тюнился под реальный
  RPM-тариф — пользователь решил, что тариф (Gemini Pro Max) не блокер,
  но фактическое значение стоит сверить после первого реального прогона.
- Существующие postgres-gated тесты, которым я только поправил
  захардкоженный ID головы (`test_v54_materialization_postgres.py` и
  ещё 4 файла) — не перепрогонялись на реальном Postgres в рамках этой
  сессии за пределами строкового изменения; сама миграционная цепочка
  для них уже подтверждена в §3 (та же цепочка, тот же финальный revision).

## 7. Живой smoke-тест на production Gemini API (2026-09-13) — найден и исправлен реальный баг схемы

Прогнан `extract_combined_fields_with_gemini()` напрямую (в обход
`document_extraction.py`, без regex-предфильтра/fallback/пула — чистая
проверка вызова+схемы) на трёх синтетических текстах, с реальным
production-ключом из `/opt/pu-workspace-primary/shared/.env.primary`
(прочитан одной командой в `docker run --env-file`/`-e`, ни разу не
выведен в чат/лог/вывод команды).

### Первый прогон — 100% отказ, не связано с ключом или моделью

Все три текста упали с одинаковой ошибкой:
```
400 Bad Request — INVALID_ARGUMENT
"Invalid JSON payload received. Unknown name \"type\" at
 'generation_config.response_schema.properties[0].value.items.properties[2..8].value':
 Proto field is not repeating, cannot start list."
```
Проверил отдельно: ошибка идентична и на `gemini-3.8-flash` (модель из
прод-конфига), и на `gemini-3.6-flash` (дефолт кода) — значит дело не в
имени модели, а в самой схеме `COMBINED_EXTRACTION_SCHEMA`.

**Корневая причина**: `properties[0]` = `obligations`, `properties[2..8]`
— ровно 7 nullable-полей (`due_date`, `due_date_evidence_quote`,
`assignee_hint`, `assignee_evidence_quote`, `amount`, `amount_currency`,
`amount_evidence_quote`), объявленных как `"type": ["string"/"number", "null"]`
(JSON-Schema-style union). `responseSchema` у Gemini — protobuf-based
(`Schema`-сообщение, общее с Vertex AI), **не принимает `"type"` как
список** — несмотря на то, что официальный туториал
[ai.google.dev/gemini-api/docs/structured-output](https://ai.google.dev/gemini-api/docs/structured-output)
буквально показывает именно такую форму (`{"type": ["string", "null"]}`).
Расхождение документации с реальным поведением API подтверждено и
[форумом Google](https://discuss.ai.google.dev/t/responseschema-and-json-schema-specs-of-type-as-array/61211)
(открыто с июня 2025, без официального фикса) — ни одна из существующих
в этом файле схем (`ANALYSIS_SCHEMA`, `MESSAGE_REPLY_SCHEMA`) раньше не
использовала union-тип, поэтому баг не проявлялся до `COMBINED_EXTRACTION_SCHEMA`.

**Подтверждённый рабочий синтаксис** — одиночный `"type"` + отдельный
булев `"nullable": true`, по образцу из
[github.com/google-gemini/generative-ai-js issue #188](https://github.com/google-gemini/generative-ai-js/issues/188):
```json
{"type": "STRING", "nullable": true}
```
(регистр `type` не важен — существующие рабочие схемы в этом файле уже
используют lowercase `"string"`, сохранил этот стиль).

### Фикс

`_OBLIGATION_ITEM_SCHEMA` (`app/gemini_analysis.py`): все 7 полей —
`"type": ["string", "null"]` → `"type": "string", "nullable": True`
(аналогично `"number"` для `amount`). Остальная логика
(verbatim-проверка, fallback, пул воркеров) не тронута.

### Повторный прогон — успех по всем трём текстам

| Текст | Результат | Время |
|---|---|---|
| 1 (богатый: поручение+срок+ответственный+риск+решение+ответ) | Все 4 категории корректны; `due_date=2026-10-20` (verbatim PASS); `assignee_hint="Смирнов А.В."` — совпало с переданным `member_names` (verbatim PASS); `amount=null` — сумма в тексте была не в этом же предложении, модель разумно не привязала её к обязательству | 3.01с |
| 2 (пустой, негативный контроль) | Все 4 массива — `[]`, никаких галлюцинаций | 1.40с |
| 3 (ловушка: дата-ссылка на СНиП/152-ФЗ) | **`due_date=null`** (не `2003-02-23`/`2006-07-27`) — ключевая проверка пройдена; `assignee_hint=null` («ответственный… не назначен»); модель дополнительно (не запрашивалось, но разумно) завела riск/decision по неназначенному ответственному и незафиксированной стоимости | 1.77с |

**Все evidence-цитаты** во всех трёх текстах прошли программную
verbatim-проверку (`quote.casefold() in text.casefold()`) — ни одного
перефразирования, ни одного `FAIL`. Реальные тайминги (1.4-3.0с) —
быстрее предположения preflight §10 (2-5с), но и это лишь три вызова, не
статистика.

### Тесты-регрессии (добавлены в `tests/test_gemini_analysis.py`)

- `test_combined_extraction_schema_never_uses_type_as_a_list` — статическая
  проверка формы схемы (без сети): ни один узел `ANALYSIS_SCHEMA`/
  `COMBINED_EXTRACTION_SCHEMA` не имеет `"type"` списком. Поймала бы
  исходный баг без единого обращения к `GEMINI_API_KEY`.
- `test_nullable_obligation_fields_use_type_plus_nullable_not_a_type_list` —
  все 7 nullable-полей используют `"type"` строкой + `"nullable": True`;
  `title`/`evidence_quote`/`confidence` не помечены `nullable`.
- `test_extract_combined_fields_propagates_the_exact_production_schema_error` —
  мок с буквальным телом ответа, который вернул prod, подтверждает, что
  `extract_combined_fields_with_gemini` не глотает такую ошибку (fallback
  в `document_extraction.py` зависит именно от того, что исключение
  доходит до вызывающего кода).

Скрипт самого smoke-теста (`gemini_smoke_test.py`) — одноразовый, в
scratchpad сессии, **не в репозитории**, без секретов.

Жду вашего подтверждения перед commit.
