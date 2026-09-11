# MVP-1 Main Integration — OCR Preflight

Дата: 2026-09-11

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед OCR: `7db7f3dcaf747dd840798dc9d0a39b3836285e0d` (Snapshots, закрыты и запушены).

Этот документ фиксирует результат read-only анализа области OCR перед selective
port из `codex/mvp1-phase2-review` в `main`, по той же схеме, что и
[`mvp1-main-integration-snapshots-preflight.md`](mvp1-main-integration-snapshots-preflight.md).
Он является планом следующей сессии. Implementation в момент составления
документа не начиналась. Код не менялся, коммит не делался.

## 1. Статус и фактический объём "OCR"

**Implementation: NOT STARTED.**

Важное отличие от Snapshots: `main` **не пустой** в области OCR. На главную
ветку уже слит большой самостоятельный пласт работы — распознавание
сканов/PDF при загрузке документов (adaptive Tesseract PSM fallback, repair
фрагментированного текста, near-match merge, XLSX-дата-конвертация,
structured-import с `__PU_SOURCE_COORD__`). Это отдельная линия эволюции
main, случившаяся **после** того, как ветка `codex/mvp1-phase2-review`
разошлась с main, и её в review вообще нет.

То, что реально отсутствует на main и есть только в review — три конкретных
модуля:

- `backend/app/ocr_quality/routing.py` — provider-neutral роутер
  local-OCR-first / опциональный external vision (`Mode`, `ExtractionPolicy`,
  `route_extraction`, `capabilities`), с явной taxonomy причин
  незавершённости (`IncompleteReason`).
- `backend/app/ocr_quality/xlsx_cells.py` — security-hardened построчный
  парсер XLSX (bounded zip/XML, formula/cached-value provenance,
  `locators` для будущей Evidence-системы).
- Точечная интеграция `route_extraction` в `organizer_engine/drive.py`
  (`DriveClient.populate_content`) и `api/workspace.py` (`_populate_content`
  fallback-путь).

Плюс улучшения существующего на main `ocr_quality/benchmark.py`
(корректный scoring, геометрическая валидация evidence).

Это и есть настоящий scope "OCR" для следующей сессии — не общий OCR-движок
(он уже есть и работает), а именно **routing-слой + XLSX cell evidence +
benchmark hardening**.

## 2. Архитектурные развилки (main vs review) — не решать сейчас, решить в реализации

Сравнение выполнено по каждому файлу, где main и review расходятся в
OCR-смежной области. Итог: часть расхождений — main ушёл вперёд после
разделения веток (review устарел, портировать нельзя — тот же паттерн, что
был со `storage.py`/`google_workspace.py` в Snapshots); часть — review
содержит реальную новую возможность; и один случай — настоящая
двусторонняя развилка, требующая явного решения перед реализацией.

### 2.1. `organizer_engine/content.py` — main строго впереди, review НЕ портировать

Diff `git diff HEAD review -- content.py`: `+24 / -342` строк — review
на 342 строки *беднее* main. У review полностью отсутствуют:

- `_ocr_fragment_penalty`, `_numeric_tokens`, `_non_fragment_corruption`,
  `_looks_tabular`, `_preserve_primary_near_matches`,
  `_merge_safe_numbered_prose`, `_safe_whole_page_fallback` — весь механизм
  adaptive PSM fallback (main: `_tesseract(..., allow_fallback=True)`;
  review: `_tesseract(path, timeout=None)` без fallback вообще).
- `_xlsx_column_index`, `_xlsx_date_style_indexes`, `_xlsx_date_value` —
  конвертация дат по Excel-стилю ячейки (см. п. 2.2).

Это работа main ПОСЛЕ разделения веток (подтверждено git log:
`e8b9897 fix: preserve readable OCR words during fallback merge`,
`931bec9 test: cover adaptive OCR fallback safety`,
`6af01c1 fix: preserve tables while repairing fragmented OCR prose`,
`c76cf33 fix: retry fragmented OCR pages with bounded PSM fallback`,
`7c1909f fix: calibrate OCR quality for short invoices`), задокументирована в
`backend/OCR_ADAPTIVE_PSM_REPORT.md`, `OCR_FRAGMENT_REVIEW_REPORT.md`,
`OCR_NEAR_MATCH_MERGE_REPORT.md` и покрыта 12 тестами в
`backend/tests/test_content_adaptive_ocr.py` (существуют только на main).

**Решение: `content.py` не заменять и не портировать поверх review-версии.**
`ExtractionResult` (dataclass) у main и review идентичны по всем полям, КРОМЕ
`spreadsheet_cells`/`spreadsheet_sheets`, которых у main нет (см. 2.2) —
их нужно добавить точечно.

### 2.2. XLSX-извлечение — настоящая двусторонняя развилка, нужно решение

Это главный содержательный вопрос preflight. Main и review решают задачу
"текст из XLSX" двумя несовместимыми, оба по-своему ценными способами.

**Main (`_xlsx_text` внутри `content.py`, строки 198-261):**
- Конвертирует числовые date-serial значения в ISO-даты по Excel style
  (`_xlsx_date_style_indexes`/`_xlsx_date_value`) — у review этого нет вообще.
- Возвращает плоский TSV с маркером `__PU_SOURCE_COORD__:{sheet}:{row}` в
  конце каждой строки — этот маркер **потребляется отдельной, тоже
  main-only фичей** `structured_import` (financial import), см.
  `backend/tests/test_structured_import.py`, `test_daily_briefing.py`,
  `test_telegram_webhook.py` — все три на 178-339 строк длиннее на main, чем
  на review (review их сильно урезал/не развивал).
- **Не имеет никакой защиты** от вредоносного XLSX: обычный
  `ElementTree.fromstring(archive.read(name))` без ограничений на
  ZIP-entry count, decompression ratio, XML depth/node count, DOCTYPE/ENTITY.
  Классический риск zip-bomb / XML entity expansion. Ни на main, ни на
  review нет ни одного теста на этот случай (`grep` по "zip bomb"/"entity
  expansion"/"DOCTYPE" по обеим веткам — пусто).

**Review (`ocr_quality/xlsx_cells.py`, самостоятельный модуль, ~330 строк):**
- Жёстко ограничивает archive size, entry count, XML depth/node count,
  compression ratio, shared-strings budget; явно отклоняет DOCTYPE/ENTITY;
  запрещает filesystem-absolute/relative-traversal targets.
- Даёт **структурную** per-cell provenance: `formula`, `cached_value`,
  `cache_state` (`present`/`missing`/`empty`), `formula_recalculated`,
  `locators` (под будущую Evidence-систему), с явным
  `identity_verified`-флагом, когда sheet/cell адрес нельзя точно
  подтвердить.
- **Не делает date-конвертацию вообще** — отдаёт сырое `cached_value`.
- **Полностью обходит** generic-пайплайн `extract_text_result`: XLSX — это
  отдельная ранняя ветка (`if suffix == "xlsx": ... return ExtractionResult(...)`),
  `.text` — простой `"\n".join(tab-separated rows)`, **без**
  `__PU_SOURCE_COORD__` — то есть буквальный порт сломает
  `test_structured_import.py` и, судя по всему, сам structured-import
  feature на main.

**Вывод: нужен явный merge-дизайн, а не выбор одной стороны.** Кандидаты
(не решаю здесь, фиксирую для утверждения перед реализацией):

1. Портировать `xlsx_cells.py` как есть для заполнения нового поля
   `ExtractionResult.spreadsheet_cells`/`spreadsheet_sheets` (аддитивно,
   review делает так же), но **не трогать** `.text` — он по-прежнему
   строится через main-версию `_xlsx_text` (с датами и
   `__PU_SOURCE_COORD__`), чтобы `structured_import` не пострадал.
2. Добавить в main-версию `_xlsx_text` те же security-границы, что есть в
   `xlsx_cells.py` (zip/XML bounds, DOCTYPE denial), не трогая
   date-конвертацию и coordinate-marker.
3. Дополнительно решить: нужна ли date-конвертация и в структурных `cells`
   тоже (`xlsx_cells.py` её не делает) — если да, это доработка модуля, а не
   чистый порт.

Пункт 1+2 вместе закрывают и security-гап, и сохраняют существующие фичи
main — но это явная реализационная работа, не тривиальный порт.

### 2.3. `app/ocr_batch.py` + `app/api/documents.py` — main строго впереди

Main вынес общую логику в именованную функцию `reprocess_unavailable_reason`
(`ocr_batch.py`), которую переиспользует и job (`reprocess_documents`), и API
(`documents.py::_ocr_capability`, отдаёт `ocr_reprocess_available` /
`ocr_reprocess_unavailable_reason` в `list_documents`/`document_card`), и
добавил 409-guard в `create_ocr_batch` при попытке батчем зареюзить
недоступные документы. У review этого функционального узла нет вообще —
`test_ocr_batch.py::test_reprocess_requires_a_reloadable_original` существует
только на main. **Не портировать, review устарел.**

### 2.4. `jobs/handlers.py`, `staging/gmail_a05.py` — диффы это шум, не OCR

Оба файла попали в первичный grep только по одной случайной строке
("documents.ocr" job kind — идентична на обеих ветках; строка `"ocr": False`
в metadata-словаре gmail-вложения). Реальный diff `handlers.py` целиком про
**несуществующие на main** review-фичи (`app.provider_actions.product`,
`app.organizer_engine.managed_copies`,
`app.organizer_engine.storage_mutation_jobs`, `app.gmail_history`,
`app.mvp3.meeting_digest`) — тот же паттерн, что `catalog.py`/`storage.py` в
Snapshots. **Исключить полностью, не трогать.**

### 2.5. Целый пласт review-тестов вне scope — отдельная будущая веха, не OCR

`test_v7_xlsx_retention_recovery.py`, `test_v7_meeting_retained_evidence.py`,
`test_v7_meeting_commit_fault_postgres.py`, `test_mvp1_xlsx_durable_evidence.py`
зависят от `app.source_evidence.*` (`fragment_reader`, `materialization`,
`product`, `xlsx_ingestion`), `app.mvp3.meeting_digest`,
`app.mvp3.meeting_source_binding`, `app.local_upload_staging` —
ни одного из этих модулей на main нет. Это отдельная, гораздо большая веха
("v7", durable Evidence retention для загруженных документов и совещаний),
не относящаяся к OCR routing/XLSX cell extraction. **Исключить из OCR-scope
полностью.**

`test_mvp1_xlsx_cell_evidence.py` — пограничный случай: тестирует
непосредственно `content.extract_text_result`/`xlsx_cells.py` (портируемо),
но одна проверка формы (`SheetCellLocator.model_validate(locator)`) берёт
pydantic-модель из того же недостающего `app.source_evidence.fragment_reader`.
**Портировать с адаптацией** — убрать/заменить только эту одну проверку
формы локальной инлайн-проверкой набора ключей, остальное годится как есть.

### 2.6. Vision-routing — рабочая инфраструктура, но полностью неактивная

`route_extraction(..., mode="vision"/"both", ...)` дергает
`adapter.analyze_document_image(...)` через `getattr`-duck-typing. Проверено
по всему дереву **обеих** веток: ни один конкретный адаптер (`GeminiAIAdapter`
в `integrations/ai.py` и любой другой) метод `analyze_document_image` не
реализует — ни на main, ни на review. `ExtractionPolicy`/`ai_adapter` нигде
не собираются из реального project-level AI-policy — ни в
`storage_for_project`, ни в `_build_snapshot`, ни где-либо ещё в review.

Это значит: порт `routing.py` даёт немедленную пользу через local-OCR-first
контракт и `IncompleteReason`-taxonomy (упорядочивает уже существующую
local-OCR-логику под единый интерфейс), но **vision-путь остаётся мёртвым
кодом** до отдельной, не входящей в этот scope задачи — добавить
`analyze_document_image` в конкретный AI-адаптер и решить, откуда брать
`ExtractionPolicy` на уровне проекта. Явно фиксирую это, чтобы следующая
сессия не считала vision-routing "почти готовой фичей".

## 3. Обязательная матрица проверок

| Инвариант | Тест | Состояние до реализации |
|---|---|---|
| Local-режим никогда не отправляет байты во внешний адаптер | `test_mvp1_ocr_vision_routing.py::test_local_mode_never_sends_document_to_external_adapter` | Есть только в review; портируется без адаптации |
| Явный запрос vision при выключенной политике — explicit incomplete, не тихий fallback | `test_mvp1_ocr_vision_routing.py::test_requested_vision_is_explicitly_incomplete_when_policy_disables_egress` | Есть только в review; портируется без адаптации |
| Явно разрешённая внешняя политика идёт строго через AI-адаптер | `test_mvp1_ocr_vision_routing.py::test_explicit_external_policy_routes_only_through_ai_provider_adapter` | Есть только в review; портируется без адаптации |
| Недоступная внешняя способность — fail-closed с явной причиной | `test_mvp1_ocr_vision_routing.py::test_unavailable_external_capability_fails_closed_with_reason` | Есть только в review; портируется без адаптации |
| Snapshot-пайплайн (`_populate_content`) использует роутер и сохраняет incomplete_reason | `test_mvp1_ocr_vision_routing.py::test_mvp1_snapshot_pipeline_uses_router_and_preserves_incomplete_reason` | Есть только в review; портируется без адаптации (main уже даёт `item.provider_metadata` после Snapshots-фазы) |
| Публичный результат без evidence не может пройти acceptance gate | `test_ocr_benchmark_public_evidence.py::test_public_result_without_evidence_cannot_pass` | Есть только в review; портируется без адаптации |
| Evidence вне границ страницы (геометрия bbox) не проходит | `test_ocr_benchmark_public_evidence.py::test_public_out_of_page_evidence_cannot_pass` | Есть только в review; портируется без адаптации |
| Упавшая страница считается как полный miss (fn), а не пропадает из recall | `test_ocr_benchmark_public_evidence.py::test_failed_page_counts_all_expected_fields_as_missed` | Есть только в review; портируется без адаптации |
| Валидный публичный evidence засчитывается | `test_ocr_benchmark_public_evidence.py::test_public_valid_evidence_is_scored` | Есть только в review; портируется без адаптации |
| Испорченные страницы не засчитываются как technical success | `test_ocr_benchmark_public_evidence.py::test_malformed_pages_cannot_count_as_technical_success` | Есть только в review; портируется без адаптации |
| Formula cache и exact locator отделены от legacy TSV | `test_mvp1_xlsx_cell_evidence.py::test_formula_cache_and_exact_locator_are_separate_from_legacy_tsv` | Есть только в review; портируется с адаптацией (убрать `SheetCellLocator` из недостающего `source_evidence`) |
| Formula без cached value требует review, не изобретает результат | `test_mvp1_xlsx_cell_evidence.py::test_formula_without_cached_value_requires_review_without_inventing_result` | Есть только в review; портируется с той же адаптацией |
| Adaptive PSM fallback не регрессирует (12 тестов) | `test_content_adaptive_ocr.py` (полный файл) | Уже есть только на main; обязателен как regression-guard после любых правок `content.py` |
| XLSX-дата-конвертация и `__PU_SOURCE_COORD__` не регрессируют | `test_content.py::test_extracts_xlsx_dates_and_preserves_empty_columns`, `test_structured_import.py` | Уже есть только на main; обязателен как regression-guard при любом изменении XLSX-пайплайна (см. 2.2) |
| `reprocess_unavailable_reason` / UI-экспозиция доступности OCR не регрессирует | `test_ocr_batch.py::test_reprocess_requires_a_reloadable_original`, `test_ocr_batch.py::test_documents_ui_exposes_bulk_and_single_document_ocr` | Уже есть только на main; обязателен как regression-guard |
| XLSX security hardening (zip-bomb/entry-count/XML depth/DOCTYPE) | Тест отсутствует на обеих ветках | Обязательный пробел: добавить, независимо от решения по п. 2.2 |
| Существующий OCR commercial-hardening / v5.4 benchmark gate не регрессирует | `test_ocr_commercial_hardening.py`, `test_v54_ocr_benchmark.py` | Идентичны на main и review; уже PASS, без изменений не трогать |

Решение области: main самостоятельно продвинулся в document-OCR дальше, чем
review, почти везде кроме трёх модулей (`routing.py`, `xlsx_cells.py`,
`benchmark.py`-улучшений). Нужно перенести именно их, точечно интегрировать в
`drive.py`/`workspace.py`/`content.py`, не заменяя более развитый main-код, и
закрыть XLSX security-гап explicit решением из п. 2.2.

## 4. Уровни доказательства

### Offline / synthetic

Все перечисленные в п. 3 тесты — чистый offline/synthetic уровень (fake
adapters, in-memory XLSX-фикстуры, никакого реального Tesseract/AI-провайдера
кроме уже существующих на main OCR-commercial/benchmark гейтов, которые сами
по себе используют локальный `tesseract` бинарник в контейнере, как и
сегодня).

### Tested runtime / PostgreSQL

**Не требуется.** В отличие от Snapshots, у OCR-порта нет новых Alembic-
миграций (см. п. 5) и нет DB-уровневой конкурентности — `routing.py`,
`xlsx_cells.py`, доработки `benchmark.py` оперируют только in-memory
dataclass'ами (`ExtractionResult`, `XlsxExtraction`) и существующими JSON-
колонками (`document.ocr_metadata`, `virtual_node.provider_metadata` —
обе уже в схеме на обеих ветках). Если в процессе реализации найдётся
причина завести PostgreSQL-specific тест (например, конкурентная запись двух
OCR-джобов в один `document.ocr_metadata`) — это отдельное решение, не
предполагаемое сейчас.

## 5. Alembic

**Новых миграций не требуется.** Все три существующие OCR-миграции
(`a31c7d8e9f20_add_document_ocr_metadata.py`,
`b72c9f13a401_add_ocr_evidence_and_review.py`,
`c83d0a24b512_merge_job_and_ocr_heads.py`) уже применены на main (текущий
head — `201286e2acd0` после Snapshots) и **байт-в-байт идентичны** версиям в
review — проверено `git diff`, пусто. `backend/app/models/document.py`
идентичен на обеих ветках — нужные колонки (`ocr_confidence`,
`ocr_review_status`, `ocr_metadata`, `ocr_updated_at`) уже есть.

Если реализация п. 2.2 (XLSX merge) решит держать `spreadsheet_cells`/
`spreadsheet_sheets` только в `ExtractionResult.metadata()` (JSON-payload
внутри существующей колонки `document.ocr_metadata`), новых колонок тоже не
понадобится — но это нужно подтвердить при реализации, не здесь.

## 6. Полный backend regression

Актуальный baseline (тот же HEAD `7db7f3d`, зафиксирован в ходе закрытия
Snapshots и не менялся с тех пор — этот preflight код не трогал):

- `1496 passed`;
- `25 skipped`;
- `1 failed` — предсуществующий, задокументированный,
  `test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision`,
  не связан ни со Snapshots, ни с OCR.

Все перечисленные в п. 3 main-only regression-guard тесты (adaptive PSM,
XLSX-даты, `reprocess_unavailable_reason`, commercial-hardening,
v5.4-benchmark) уже входят в эти `1496 passed`. После реализации нужен
новый полный прогон с фактическими числами.

## 7. Secrets scan

Кода не менялось, коммита нет — scan не выполнялся. Требуется по полному
diff перед будущим OCR-коммитом, тем же методом, что и в
[`mvp1-main-integration-snapshots-completion.md`](mvp1-main-integration-snapshots-completion.md)
п. 7 (grep по паттернам ключей/токенов плюс ручная проверка
высокоэнтропийных строк).

## 8. Предполагаемый состав будущего коммита

Фактически изменено `0` файлов. Ожидаемый минимальный набор (уточнится по
факту реализации, особенно по итогам решения п. 2.2):

- `backend/app/ocr_quality/routing.py` — новый файл, порт без адаптации;
- `backend/app/ocr_quality/xlsx_cells.py` — новый файл, порт без адаптации
  (используется независимо от решения по п. 2.2 — сам модуль self-contained);
- `backend/app/core/integration_types.py` или `contracts.py` — возможно,
  формализовать `analyze_document_image` в `AIProviderAdapter` Protocol
  (сейчас duck-typing даже в review) — отдельное решение, не обязательное;
- `backend/app/organizer_engine/content.py` — точечно: добавить
  `spreadsheet_cells`/`spreadsheet_sheets` в `ExtractionResult` и вызов
  `xlsx_cells.py`, согласно выбранному варианту из п. 2.2; не заменять файл;
- `backend/app/organizer_engine/drive.py` — точечно: `DriveClient.__init__`
  получает `extraction_mode`/`extraction_policy`/`ai_adapter`,
  `populate_content` переходит на `route_extraction`;
- `backend/app/api/workspace.py` — точечно: `_populate_content` fallback-путь
  переходит на `route_extraction`;
- `backend/app/ocr_quality/benchmark.py` — точечно: scoring-фиксы
  (fn-counting, geometric evidence validation, technical-success критерий);
- `backend/tests/test_mvp1_ocr_vision_routing.py` — новый, порт без адаптации;
- `backend/tests/test_ocr_benchmark_public_evidence.py` — новый, порт без
  адаптации;
- `backend/tests/test_mvp1_xlsx_cell_evidence.py` — новый, порт с адаптацией
  (см. 2.5);
- новый тест на XLSX security hardening (zip-bomb/XML-bounds) — с нуля, гап
  из п. 3;
- итоговый audit OCR-области (`mvp1-main-integration-ocr-completion.md`).

Это планируемый, а не финальный список.

## 9. Ограничения на реализацию

- `organizer_engine/content.py` **не заменять** review-версией — только
  точечное добавление `spreadsheet_cells`/`spreadsheet_sheets` и вызова
  `xlsx_cells.py`; весь adaptive-PSM-fallback механизм main остаётся
  нетронутым.
- `ocr_batch.py`, `api/documents.py` **не трогать** со стороны review вообще
  — main строго впереди (п. 2.3).
- `jobs/handlers.py`, `staging/gmail_a05.py` **не трогать** — diff с review
  это шум от несвязанных фич (п. 2.4).
- Файлы v7/Evidence-retention (`app.source_evidence.*`,
  `app.mvp3.meeting_*`, `app.local_upload_staging`) **не переносить** —
  отдельная веха вне scope (п. 2.5).
- Решение по XLSX merge-дизайну (п. 2.2, три кандидата) должно быть принято
  явно **до** написания кода, не по ходу дела.
- Vision-путь в `routing.py` переносится как контракт/API, но **не
  подключается** ни к какому реальному AI-адаптеру и не тестируется сверх
  того, что тестирует review (fail-closed поведение) — фиксировать это в
  итоговом audit, чтобы не создать иллюзию рабочей vision-фичи.
- Любое отклонение от этих ограничений — описать до коммита, как и в
  Snapshots.

## 10. Git-состояние на preflight

```text
git diff --check: PASS
git status --porcelain: clean
```

Код не менялся, коммит не делался. Следующая сессия должна начать с этого
документа, в первую очередь утвердить решение по п. 2.2 (XLSX merge-дизайн),
затем выполнить selective port и пройти гейты: offline regression (Alembic-
гейт и PostgreSQL tested-runtime по п. 4-5 не требуются, если фактическая
реализация не введёт новых миграций/DB-конкурентности), secrets scan,
итоговый audit.

## 11. Резолюция п. 2.2 — implementation log (эта сессия)

**Scope этой сессии — только п. 2.2 (кандидаты 1+2), явно НЕ весь план п. 8.**
Vision-роутинг (`routing.py`), доработки `benchmark.py`, `drive.py`/
`workspace.py` wiring и связанные с ними тесты (строки матрицы п. 3 про
`test_mvp1_ocr_vision_routing.py` / `test_ocr_benchmark_public_evidence.py`)
**не переносились** и остаются открытым пунктом плана — этот документ по-прежнему
актуален как preflight для той части.

### 11.1. Принятое решение (зафиксировано до кода)

- Пункт 3 кандидатов (date-конвертация внутри `xlsx_cells.py`/структурных
  `cells`) — **не делаем в этой итерации**. `xlsx_cells.py` перенесён как есть
  из `codex/mvp1-phase2-review`, без адаптации, без date-конвертации.
- Кандидаты 1+2 реализованы вместе:
  1. `xlsx_cells.py` заполняет новые аддитивные поля
     `ExtractionResult.spreadsheet_cells`/`spreadsheet_sheets`
     (и, соответственно, `metadata()["spreadsheet_cells"/"spreadsheet_sheets"]`).
     `.text` **не тронут** — по-прежнему строится main-версией `_xlsx_text`
     (date-конвертация и `__PU_SOURCE_COORD__` сохранены byte-for-byte,
     подтверждено regression'ом п. 11.3).
  2. В main-версию `_xlsx_text`/`_xlsx_date_style_indexes` добавлены те же
     security-границы, что в `xlsx_cells.py`: archive-size limit, entry-count
     limit, decompression-ratio/unpacked-size limit (zip-bomb), XML
     node-count/depth limit и явный DOCTYPE/ENTITY denial (XML-entity
     expansion) — новый класс `XlsxSecurityError(ValueError)` в `content.py`,
     код ошибки content-free (например `"xlsx_xml_declaration_denied"`),
     без изменения date-конвертации и coordinate-marker.
- Package, который `xlsx_cells.py` не может структурно верифицировать
  (например, worksheet без `xl/_rels/workbook.xml.rels`, или тестовый
  fixture с плейсхолдер-namespace вместо реального OOXML), **не ломает**
  `.text`: `extract_xlsx_cells` оборачивается в try/except
  `XlsxExtractionError`, `spreadsheet_cells`/`spreadsheet_sheets` остаются
  пустыми, добавляется content-free warning
  `spreadsheet_cells_unavailable:<code>` и `needs_review=True`.

### 11.2. Коррекция к п. 3 preflight

Строка матрицы п. 3 предполагала адаптацию review-теста через "убрать
`SheetCellLocator` из недостающего `source_evidence`" — при реализации
выяснилось, что `app/source_evidence/fragment_reader.py` (с `SheetCellLocator`)
**уже есть на main** (не портировался в рамках Snapshots/Google OAuth веток,
но присутствует). Адаптация не потребовалась: новый
`backend/tests/test_xlsx_cells.py` использует `SheetCellLocator.model_validate`
напрямую, как и review-версия теста.

### 11.3. Фактически изменённые/новые файлы

- `backend/app/ocr_quality/xlsx_cells.py` — новый, порт без адаптации
  (byte-for-byte из `codex/mvp1-phase2-review`, коммиты `9494281`/`f7fc07c`).
- `backend/app/organizer_engine/content.py` — точечно: `XlsxSecurityError`,
  `_xlsx_bounded_zip`/`_xlsx_bounded_xml` helpers, переключение
  `_xlsx_text`/`_xlsx_date_style_indexes` на них, новые поля
  `ExtractionResult.spreadsheet_cells`/`spreadsheet_sheets`, вызов
  `extract_xlsx_cells` в `extract_text_result`. Adaptive-PSM-fallback и весь
  OCR-механизм не тронуты.
- `backend/tests/test_content.py` — 8 новых тестов: аддитивность
  `spreadsheet_cells`/`spreadsheet_sheets` при неизменном `.text`; graceful
  degradation на структурно-неверифицируемом пакете; zip-bomb
  (compression-ratio), entry-count limit, archive-size limit, XML
  depth-limit, DOCTYPE/ENTITY denial (2 варианта payload).
- `backend/tests/test_xlsx_cells.py` — новый, 37 тестов, адаптированы из
  `test_mvp1_xlsx_cell_evidence.py` review-ветки: убраны кейсы, специфичные
  для `content.extract_text_result`/legacy TSV (перенесены в
  `test_content.py`), добавлен явный тест
  `test_no_date_conversion_cached_value_stays_raw_serial`, фиксирующий
  решение п. 11.1 как regression-guard.

### 11.4. Offline / synthetic regression — фактические числа

Контейнер `app-backend:cf06cf21544a428ab13876f1273c7ca53128fe9d` (тот же
образ, что крутится в проде), запущен эфемерно, `--network none`,
`DATABASE_URL=sqlite:///:memory:`, без реальных секретов.

- `test_content.py` (23, включая 8 новых) — **PASS**.
- `test_xlsx_cells.py` (37, новый) — **PASS**.
- `test_structured_import.py`, `test_daily_briefing.py`,
  `test_telegram_webhook.py`, `test_ocr_batch.py` (32) — **PASS**, `.text`
  и `__PU_SOURCE_COORD__` не регрессировали.
- `test_content_adaptive_ocr.py`, `test_ocr_commercial_hardening.py`,
  `test_v54_ocr_benchmark.py` (29, реальный `tesseract` в контейнере,
  200.75s) — **PASS**, adaptive-PSM-fallback не регрессировал.
- Полный `backend/tests/` (1540 passed, 25 skipped, 525.67s): **1 failure**,
  `test_mvp1_google_storage_oauth.py::test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision`
  (`AttributeError: '_IncludedRouter' object has no attribute 'path'`).
  Воспроизведено на `git stash` (без изменений этой сессии) — **pre-existing**,
  относится к коммиту `e9e03ad` (Google Drive storage OAuth), не к OCR/XLSX.
  Не входит в scope этой сессии, не блокирует коммит по п. 2.2.

### 11.5. PostgreSQL tested runtime

**Не требуется**, подтверждено явно при реализации: изменения — только
`content.py` (in-memory `ExtractionResult`/dataclasses) и новый
`ocr_quality/xlsx_cells.py` (in-memory `XlsxExtraction`). Новых Alembic-
миграций, новых DB-колонок и DB-уровневой конкурентности не введено —
`spreadsheet_cells`/`spreadsheet_sheets` живут только в
`ExtractionResult.metadata()` (JSON внутри уже существующей
`document.ocr_metadata`, см. п. 5), без новой колонки.

### 11.6. Secrets scan

Выполнен по фактическому diff'у (`git diff` + новые файлы), тем же методом,
что в Snapshots: grep по паттернам ключей/токенов
(`api[_-]?key|secret|token|password|-----BEGIN|AKIA...|ghp_...|xox[baprs]-|AIza...`)
и по высокоэнтропийным base64-подобным строкам ≥32 символов.

Единственное совпадение по `secret` — синтетическая XML-полезная нагрузка в
тестах (`<!ENTITY secret "private">`), намеренно используемая, чтобы
доказать, что DOCTYPE/ENTITY никогда не резолвится; не является реальным
секретом. Высокоэнтропийных строк не найдено. **Чисто.**

### 11.7. Ограничения — соблюдены

Все пункты п. 9 выполнены: `content.py` не заменён (только точечные диффы),
`ocr_batch.py`/`api/documents.py`/`jobs/handlers.py`/`staging/gmail_a05.py`
не тронуты, `source_evidence.*`/`mvp3.meeting_*`/`local_upload_staging` не
переносились (только read-only использование уже существующего
`SheetCellLocator` в тесте), решение по п. 2.2 принято явно до кода (см.
диалог сессии), vision-путь не переносился вообще в этой сессии.

Коммит по итогам п. 11 ещё не сделан — ждёт подтверждения пользователя.
