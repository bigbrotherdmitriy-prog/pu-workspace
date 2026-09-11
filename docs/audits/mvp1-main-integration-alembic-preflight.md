# MVP-1 Main Integration — Alembic Preflight

Дата: 2026-09-11

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед Alembic: `c586583a4eb290a203fdb9e73507d727f0254255` (App
composition, закрыта и запушена).

Этот документ фиксирует результат read-only анализа Alembic-цепочки миграций
перед решением по `codex/mvp1-phase2-review` (HEAD `4cd0a62`), по той же
схеме, что и пять предыдущих preflight-документов в этой серии. **Код не
менялся, коммит не делался.**

## 1. Ответы на три прямых вопроса задания

### 1.1. Единственная ли сейчас head?

**Да.** `alembic -c alembic.ini heads` (реальный Alembic, не парсинг
regex'ом) возвращает ровно одну запись:

```
201286e2acd0 (head)
```

Это уже покрыто существующим, уже проходящим тестом
`backend/tests/test_schema_revision.py::test_expected_schema_revision_matches_single_alembic_head`,
который явно требует `len(heads) == 1` через `alembic.script.ScriptDirectory`
(не через хардкод) — не гипотетическая проверка, а реальный regression-guard,
уже в составе полного прогона, который проходил все шесть предыдущих раз.

Исторически на main было **два** branch/merge-события (не проблема, а уже
разрешённая история):
- `a31c7d8e9f20 (branchpoint)` → 3 потомка (`b71d2e4f9a10` harden background
  jobs, `b72c9f13a401` add OCR evidence and review state, `b01c9a7d4e21` add
  provider-neutral mail client draft state) → первые два сходятся в
  `c83d0a24b512 (mergepoint)`, третий продолжает отдельную цепочку.
- `e74a1c5d09b2 (mergepoint)` — сливает "v5.4 trust runtime" ветку
  (заканчивающуюся на `a54f001c0a09`) с "production MPP schema" веткой
  (заканчивающуюся на `c05e2f3a4b56`, потомком той самой отдельной цепочки
  от `b01c9a7d4e21`).

Обе точки слияния — пустые no-op merge-миграции (`upgrade()`/`downgrade()`
без реальных DDL-операций, только объединение графа). Текущая цепочка после
этого — линейна вплоть до head.

### 1.2. Нет ли расхождений в `CURRENT_SCHEMA_REVISION`?

**Нет расхождений.** Единственный источник истины — `backend/app/schema.py`:

```python
CURRENT_SCHEMA_REVISION = "201286e2acd0"
```

Импортируется напрямую (`from app.schema import CURRENT_SCHEMA_REVISION`) в
`core/readiness.py` (readiness-проверка `checks["schema"]`) и минимум в 10
тестовых файлах, включая `test_schema_revision.py`, который **assert'ит
равенство с фактическим Alembic head**, а не сравнивает две независимые
константы. Значение совпадает с фактическим head (`201286e2acd0`) точно.
Ни отдельной "readiness-специфичной" константы, ни рассинхронизации нет.

Ту же схему (единый источник + тест-на-равенство-с-head) использует и
review — его `schema.py` содержит `CURRENT_SCHEMA_REVISION = "a54f001c0a24"`,
и совпадает со **своим** head. Обе ветки внутренне непротиворечивы; расхождение
— только *между* ветками (ожидаемо, разный набор миграций), не *внутри*
одной.

### 1.3. Какие review-миграции ещё не перенесены?

Полный список файлов только на review, за вычетом файлов только на main
(`diff` по `backend/migrations/versions/`), раскладывается на два разных
случая — **это критично не смешивать**:

**Уже перенесены с новым revision ID (не считать как "непортированные")** —
подтверждено байт-в-байт идентичным содержимым `upgrade()`/`downgrade()`
(различия только в форматировании/docstring):

| Review revision | Main revision | Область | Подтверждено в |
|---|---|---|---|
| `a54f001c0a22_add_mvp1_storage_oauth_state` | `e16a1c2d3f40_add_mvp1_storage_oauth_state` | OAuth | `e9e03ad` |
| `a54f001c0a23_add_snapshot_metadata_envelope` | `21d8f9354c18_add_snapshot_metadata_envelope` | Snapshots | `7db7f3d` |
| `a54f001c0a24_add_snapshot_shortcut_metadata` | `201286e2acd0_add_snapshot_shortcut_metadata` (текущий head) | Snapshots | `7db7f3d` |

**Действительно не перенесены** — 12 миграций, `a54f001c0a10` …
`a54f001c0a21`, линейная цепочка review от того же общего предка
(`a54f001c0a09_v54_deadline_precision`, есть на обеих ветках байт-в-байт).
Разбор — п. 2.

Также стоит явно исключить main-only файлы, которые в diff'е выглядят как
"на review их нет" — это не пробел, а main строго впереди:
`b01c9a7d4e21_add_mail_client_draft_state`, `b42d7f9a1c35_add_project_site_location`,
`c04d1e2f3a45_add_mail_user_settings`, `c05e2f3a4b56_add_mpp_schedule_sources`,
`c24d9e7a61b0_add_cash_flow_dimensions`, `c93b7f4a21d0_mvp3_cas_history_conflicts`,
`d04e8a6c31f2_mvp3_notification_policy`, `e74a1c5d09b2_merge_v54_and_mpp_heads`,
`e75c4a1d9f20_harden_mvp4_finance_events`, `f18b7c42d9a1_add_schedule_planner_fields`
— все они на review отсутствуют потому, что review — параллельная ветка,
никогда их не видевшая (та же картина, что уже задокументирована в
Storage adapters/App composition preflight для не-Alembic файлов).

## 2. Архитектурные развилки — главный результат этого preflight

**Это не "12 миграций, которые надо перенести" в мехническом смысле.**
Main и review **независимо** продолжили расширять одни и те же базовые
таблицы (`obligations`, `risks`, `decisions`, `meetings`, `project_contacts`,
`cash_flow_entries`, `budget_lines`) после общего предка `a54f001c0a09` — с
разными столбцами и разным дизайном для *концептуально похожих* задач
(optimistic-concurrency versioning, append-only audit history,
evidence-pinning/review-workflow). Проверено содержимое каждой из 12 миграций
против фактических изменений main в том же диапазоне — результат:

### 2.1. Реальные коллизии имён колонок (4 из 12) — требуют явного решения, не порта

| Review-миграция | Коллизия | С чем на main |
|---|---|---|
| `a54f001c0a10_mvp3_management_foundation` | `op.add_column("obligations"/"risks"/"decisions", "record_version", ...)` — колонка **уже существует** | `c93b7f4a21d0` (VERSIONED-кортеж включает `obligations`, `risks`, `decisions`) |
| `a54f001c0a12_mvp3_contact_resolution` | `op.add_column("project_contacts", "record_version", ...)` — колонка **уже существует** | `c93b7f4a21d0` (тот же VERSIONED-кортеж включает `project_contacts`) |
| `a54f001c0a14_mvp4_budget_dds_controls` | `op.add_column("cash_flow_entries", "task_id", ...)` + `fk_cash_flow_task` — колонка и constraint **уже существуют** | `e75c4a1d9f20` (`op.add_column("cash_flow_entries", sa.Column("task_id", ...))`, `op.create_foreign_key("fk_cash_flow_task", ...)`) |
| `a54f001c0a19_meeting_source_binding` | `op.add_column("meetings", "record_version", ...)` — колонка **уже существует** | `c93b7f4a21d0` (тот же VERSIONED-кортеж включает `meetings`) |

Буквальный `alembic upgrade` с любой из этих миграций поверх текущего main
head закончится `DuplicateColumn`/constraint-конфликтом в PostgreSQL —
проверено построчным сравнением, не гипотетически.

**Глубже, чем просто дубли колонок** — `a54f001c0a10` — это не только
`record_version`. Это целый параллельный дизайн append-only audit-истории:

- **Main (`c93b7f4a21d0`)**: одна полиморфная таблица `management_history`
  (`entity_type`/`entity_id`-дискриминатор, полные JSON-снапшоты
  `old_values`/`new_values`) — покрывает **всё** governance-смежное одним
  механизмом. Уже в проде, уже есть модель `ManagementHistory`.
- **Review (`a54f001c0a10`)**: две отдельные таблицы —
  `obligation_history` (state-machine: `sequence`/`event`/`from_status`/
  `to_status`/`resulting_version`) специально под obligations, и
  `governance_history` (тот же state-machine-шаблон, но для `risks`/
  `decisions` через `entity_type`/`entity_id`) — **плюс** заметно более
  богатый набор obligations-specific колонок, которых у main вообще нет:
  `due_time`, `timezone`, `deadline_policy` (JSON), `escalation_level`,
  `last_escalated_at`, `evidence_pins`, `review_state` + FK `risks`/
  `decisions` → `obligations`/`tasks` напрямую.

Это два **структурно разных** способа решать одну и ту же задачу (не
"review чуть подробнее делает то же самое main"), явно предполагающих разный
API/бизнес-логику поверх. Реконструировать один в другой, добавить review's
дизайн параллельно main's, или расширить `management_history` до
эквивалентной функциональности — решение по существу, а не техническая
формальность. Аналогичная, но меньшего масштаба, развилка — контакт-
резолюция: main's `contact_conflicts` (отдельная таблица per-conflict-row)
vs review's `resolution_state`/`resolution_reason_code` (инлайн-состояние
прямо на `project_contacts`) — два способа моделировать один процесс.

### 2.2. Чистые, аддитивные, без коллизий (8 из 12) — но требуют кода, которого нет

| Review-миграция | Что делает | Нужный, отсутствующий на main модуль |
|---|---|---|
| `a54f001c0a11_contract_version_history` | Новая таблица `contract_versions` (без FK на `contracts`, намеренно — история должна пережить удаление ошибочного черновика) | `app.models.organization_contract.ContractVersion` |
| `a54f001c0a13_mvp3_search_saved_views` | 2 новые таблицы | `app.models.search` (не существует на main, см. App composition preflight) |
| `a54f001c0a15_provider_product_outbox` | **Не создаёт таблиц** (имя файла вводит в заблуждение) — только `DROP`+`CREATE CHECK CONSTRAINT ck_v54_provider_confirm_synthetic` на уже существующей `v54_provider_actions`, расширяя CONFIRM-режим с синтетики на реальные `google_workspace`-действия (`gmail.message.send`, `google.tasks.upsert`, `google.calendar.upsert`). Проверено: main после общего предка эту constraint больше не трогает — применилась бы чисто. Но это explicit product-решение ("разрешить реальные provider actions в CONFIRM"), не техническая формальность | — (нет нового кода, но нужно решение product-уровня) |
| `a54f001c0a16_mvp4_supply_controls` | 3 новые таблицы | `app.mvp4.supply.models` (не существует на main, см. App composition preflight) |
| `a54f001c0a17_management_digest_preferences` | 2 новые таблицы | `app.models.management_digest` (не существует на main) |
| `a54f001c0a18_gmail_history_checkpoint` | 2 новые таблицы | `app.models.mailbox_identity.GmailHistoryCheckpoint`/`GmailHistoryCheckpointEvent` (main имеет соседний `mailbox_identity`-функционал из v54, но не эти две таблицы) |
| `a54f001c0a20_schedule_graph_intent` + `a54f001c0a21_schedule_wbs_hierarchy` (пара, вторая зависит от первой) | Новые колонки на `schedule_baselines`/`schedule_items` (`graph_revision`, `planning_mode`, `wbs_parent_id`, и т.д.) — ни одна не пересекается с main's собственными schedule-миграциями (`f18b7c42d9a1 add schedule planner fields` и др., проверено построчно) | `app.execution_forecast`/расширение schedule-моделей (см. App composition preflight — `execution_forecast` router тоже не существует на main) |

Все 8 соответствуют модулям/фичам, уже помеченным как непостроенные на main
в [App composition preflight](mvp1-main-integration-app-composition-preflight.md)
(`app.mvp4.supply`, `app.api.search`/`app.models.search`,
`app.models.management_digest`, `execution_forecast`) — это независимое,
Alembic-уровня подтверждение того же вывода с другой стороны (схема, а не
router registration).

### 2.3. Итог по 2.1/2.2

Нет ни одной миграции из 12, которую можно перенести prisma-style "как
есть". Четыре — прямая коллизия, требующая архитектурного решения по
дизайну (не только имени колонки). Восемь — синтаксически чистые, но
бессмысленные без соответствующего кода уровня приложения, которого на main
попросту нет (и port которого — отдельная, каждая своя, future-веха, не
Alembic-задача).

## 3. Обязательная матрица проверок

| Инвариант | Тест | Состояние |
|---|---|---|
| Ровно один Alembic head | `test_schema_revision.py::test_expected_schema_revision_matches_single_alembic_head` | **Уже PASS**, уже в составе каждого из шести прогонов полного regression в этой серии — не нужен новый тест |
| `CURRENT_SCHEMA_REVISION` совпадает с фактическим head | Тот же тест (единая assert) | **Уже PASS** |
| Offline-миграция не отключает application-логгеры | `test_schema_revision.py::test_offline_migration_does_not_disable_application_loggers` | **Уже PASS** |
| Полная цепочка применяется на реальном PostgreSQL без ошибок | Нет прямого теста в этом наборе — косвенно проверяется в CI (`alembic upgrade head` перед стартом контейнера, см. `docker-compose.yml`/entrypoint) | Не Alembic-специфичный regression-guard этой сессии, уже часть деплой-пайплайна |
| Ни одна из 12 непортированных миграций не коллизирует при попытке слепого применения | Тест отсутствует на обеих ветках — и не должен существовать, пока нет решения по 2.1/2.2: тест "миграция X не конфликтует" бессмыслен без выбранного дизайна для 4 коллизий | Явный, задокументированный gap — не тестовый пробел, а pending product/schema decision |

Никакой новой обязательной проверки эта сессия не добавляет — единственный
релевантный инвариант этой области уже покрыт существующим, стабильно
проходящим тестом.

## 4. Уровни доказательства

### Offline / synthetic

`alembic heads`/`alembic history`/`alembic branches` — чистый offline,
не требует БД вообще (работают по graph'у файлов миграций). Подтверждено
через реальный Alembic CLI внутри `app-backend`-образа (не regex-парсинг).
`test_schema_revision.py` — offline (SQLite in-memory для одного теста;
второй тест генерирует SQL офлайн через `command.upgrade(..., sql=True)`,
не подключаясь к реальной БД).

### Tested runtime / PostgreSQL

Полный `alembic upgrade head` **не выполним на SQLite** — цепочка
использует PostgreSQL-специфичный DDL (`ALTER TABLE ... ALTER COLUMN ...
SET NOT NULL` и подобное), подтверждено эмпирически (попытка в этой сессии
упала на `sqlite3.OperationalError: near "ALTER": syntax error` на
`organizer_proposals.idempotency_key`). Это ожидаемо и не новость — цепочка
никогда не заявляла SQLite-совместимость, только PostgreSQL; полная
верификация `upgrade head` на реальном PostgreSQL — часть штатного деплоя,
не специфичная задача этой сессии. Для 12 непортированных миграций
PostgreSQL tested-runtime потребуется **после** того, как решения по п. 2.1
приняты и появится код (не раньше).

## 5. Secrets scan

Код не менялся, коммита нет — не выполнялся. Не требуется, пока нет
изменений.

## 6. Предполагаемый состав будущей работы (не одного коммита — минимум четыре независимых)

В отличие от предыдущих пяти областей, здесь нет одного самодостаточного,
низкорискового куска для немедленной реализации — весь список либо требует
архитектурного решения (2.1), либо тянет за собой целый неотстроенный
модуль уровня приложения (2.2), который сам по себе — отдельная будущая
область:

- **Коллизии (2.1, 4 миграции)** — решение по каждой из двух развилок
  (append-only history design: расширять `management_history` vs
  добавлять `obligation_history`/`governance_history`; contact resolution:
  `contact_conflicts` vs inline `resolution_state`) должно быть принято
  явно, до какой-либо реализации.
- **`contract_versions`/`a54f001c0a15`-политика (2.2, низкий риск)** —
  можно переносить независимо от остального: `contract_versions` не имеет
  зависимостей на неотстроенный код; `a54f001c0a15`'s constraint-relax —
  чистое product-решение "разрешить реальные Google Workspace provider
  actions в CONFIRM", а не миграция, ожидающая кода.
- **`search`/`mvp4.supply`/`management_digest`/`gmail_history_checkpoint`/
  `schedule_graph_intent`+`wbs_hierarchy` (2.2, остальные 6-7)** — каждая
  ожидает свой собственный будущий preflight/область на уровне
  приложения (модели, API, тесты); Alembic-порт без этого кода создаёт
  неиспользуемые таблицы.

Никакого немедленного коммита из этого preflight не следует — в отличие от
предыдущих пяти областей, где хотя бы часть решений вела прямо к
implementation, здесь итог явно "нужны отдельные решения по каждому пункту
до реализации чего-либо".

## 7. Ограничения на реализацию

- Ни одна из 4 коллизионных миграций (2.1) не переносится без явного
  решения о дизайне (не просто переименования колонки — reconciliation
  двух разных схемных подходов).
- Ни одна из миграций, требующих кода уровня приложения (2.2, кроме
  `contract_versions`/`a54f001c0a15`), не переносится раньше, чем
  соответствующая область (search, MVP4 supply, management digest, Gmail
  history checkpoint, execution forecast) получит свой собственный
  preflight.
- Существующая цепочка main (`4d7fb326d458` … `201286e2acd0`) не
  трогается, не переупорядочивается, не сквошится — три уже перенесённых
  миграции (OAuth/Snapshots) остаются под своими main-native revision ID
  (`e16a1c2d3f40`/`21d8f9354c18`/`201286e2acd0`), не переименовываются в
  review's `a54f001c0a22-24`.
- Любое отклонение от этих ограничений — описать до коммита, как и в
  предыдущих пяти областях.

## 8. Git-состояние на preflight

```text
git diff --check: PASS
git status --porcelain: clean
```

Код не менялся, коммит не делался. Это последняя из шести запланированных
областей (OAuth, Snapshots, OCR, Storage adapters, App composition,
Alembic) — в отличие от предыдущих пяти, здесь нет самодостаточного
низкорискового куска, готового к реализации без дополнительных решений
пользователя: 4 коллизии требуют архитектурного выбора дизайна, 6-7
"чистых" миграций требуют code-level областей, которых нет и не
планировались в рамках этой серии. Единственный кандидат на независимую
реализацию без дополнительных зависимостей — `contract_versions`
(`a54f001c0a11`) и constraint-relax `a54f001c0a15` — но и по ним нужно
явное решение "делать ли вообще", не техническая заглушка.

## 9. Резолюция — implementation log (эта сессия)

### 9.1. Решения по всем четырём пунктам (зафиксированы до кода)

1. **4 коллизирующие миграции (2.1) — НЕ переносятся.** Main продолжает
   работать с `management_history`. Review-дизайн
   (`obligation_history`+`governance_history`) зафиксирован как
   задокументированная альтернатива для рассмотрения при разработке
   MVP-3 — **не реализован**. Полное, детальное описание обеих архитектур
   (схема, модели, реальное использование на каждой ветке, построчное
   сравнение по критериям, плюсы/минусы каждой, без выбора) —
   [`mvp1-management-history-architecture-comparison.md`](mvp1-management-history-architecture-comparison.md).
   Это чистая документация, не решение — явно помечено как таковое.
2. **8 code-less миграций (2.2, кроме п. 3/4 ниже) — НЕ переносятся.**
   `search`/`mvp4.supply`/`management_digest`/`gmail_history_checkpoint`/
   `schedule_graph_intent`+`schedule_wbs_hierarchy` — появятся естественно
   вместе со своими будущими MVP-областями, не как побочный продукт
   Alembic-порта.
3. **`contract_versions` (`a54f001c0a11`) — перенесён.** Единственный
   по-настоящему низкорисковый, самодостаточный кандидат: новая таблица,
   без FK-конфликтов с уже существующими колонками, без зависимости на
   неотстроенный код.
4. **Constraint-relax на `v54_provider_actions` (`a54f001c0a15`) — НЕ
   тронут.** Продуктовое решение о разрешении реальных Google Workspace
   CONFIRM-actions требует отдельного явного обсуждения — не техническая
   часть Alembic-интеграции, не смешивается с переносом схемы.

### 9.2. Фактически изменённые/новые файлы

- `backend/migrations/versions/c13606d92787_add_contract_version_history.py`
  — новый, сгенерирован через `alembic revision` (не скопирован вручную) с
  `down_revision = "201286e2acd0"` (текущий на момент preflight head);
  тело `upgrade()`/`downgrade()` — содержимое, идентичное review's
  `a54f001c0a11` (таблица `contract_versions`, три индекса,
  `UniqueConstraint`, `CheckConstraint`), адаптированное только под
  main-формат заголовка миграции.
- `backend/app/models/organization_contract.py` — точечно: добавлен класс
  `ContractVersion` (то же определение, что на review, включая
  `before_update`/`before_delete` immutability-guard'ы) и импорты
  (`CheckConstraint`, `JSON`, `UniqueConstraint`, `event`). **Ни одного
  вызывающего кода, который бы писал в эту таблицу, не добавлено** —
  сознательно: запись версий на create/update/apply контракта — реальная
  бизнес-логика (review's diff `api/organizations_contracts.py`,
  ~15 мест), которая явно вне решения п. 3 ("новая таблица", не фича) и
  принадлежит будущей MVP-3-области.
- `backend/app/models/__init__.py` — точечно: `ContractVersion`
  зарегистрирован в импорте и `__all__`.
- `backend/app/schema.py` — `CURRENT_SCHEMA_REVISION` обновлён на
  `c13606d92787`.
- **8 файлов repo-wide alembic-head trip-wire обновлений** (тот же паттерн,
  что уже документирован в Snapshots-completion): `backend/tests/
  test_mvp1_google_metadata_contract.py`, `test_v54_deadline_precision.py`,
  `test_v54_provider_action_migration.py`, `test_v54_pilot_foundation.py`,
  `test_v54_autonomy_authorization.py`, `test_v54_materialization_postgres.py`,
  `.github/workflows/docker-smoke.yml`, `scripts/ci/v54_pilot_workflow.py`
  (+ парный `scripts/ci/test_v54_pilot_workflow.py`, который проверяет
  буквальное содержимое исходника первого файла),
  `scripts/ci/test_v54_wave3_ci_gate.py`, `scripts/ci/durable_queue/run.py`
  — везде заменено ровно `"201286e2acd0"` → `"c13606d92787"` в местах,
  явно проверяющих "текущий единственный head", **не** в местах,
  фиксирующих конкретную историческую точку цепочки (`test_mvp1_snapshot_postgres.py`
  и вторая часть assert'а в `test_mvp1_google_metadata_contract.py` —
  оставлены нетронутыми намеренно, проверено по контексту каждого).
- `docs/audits/mvp1-management-history-architecture-comparison.md` —
  новый, детальное сравнение архитектур по решению п. 1 (см. 9.1).

### 9.3. Offline / synthetic regression — фактические числа

Тот же образ (`app-backend:cf06cf21544a428ab13876f1273c7ca53128fe9d`),
эфемерный запуск, `--network none`, `DATABASE_URL=sqlite:///:memory:`.

- Целевой набор (`test_schema_revision.py` + все 6 trip-wire тестовых
  файлов) — **106 passed, 3 skipped** (PostgreSQL-only).
- `ContractVersion`-модель: прямая проверка `Base.metadata.create_all()`
  на in-memory SQLite — успешно, таблица `contract_versions` маппится и
  создаётся.
- Полный набор contract-related тестов (`test_contract_bulk_discovery.py`,
  `test_contract_document_control_flow.py`, `test_contract_source_scoring.py`,
  `test_integration_contracts.py`, `test_contract_package_api.py`,
  `test_contracts_api.py`, `test_contract_roles.py`,
  `test_organization_requisites.py`, `test_document_contract_control_acceptance.py`)
  — **44 passed** — существующий `organizations_contracts.py` не задет
  добавлением модели.
- Полный `backend/tests/`: **1545 passed** (без изменения числа — этот
  коммит не добавляет новых тестовых функций, только заменяет литералы
  внутри уже существующих trip-wire-тестов), 25 skipped, тот же 1
  pre-existing failure, 553.79s.

### 9.4. PostgreSQL migration/upgrade-downgrade dry-run

Выполнен явно, как и запрошено (не просто "не требуется"): поднят
одноразовый, изолированный `postgres:16-alpine` в отдельной Docker-сети
(без подключения к живой БД), полная цепочка миграций (`base` → `head`,
все ~67 существующих + новая) применена **с нуля** на пустой базе —
**без единой ошибки**, включая PostgreSQL-специфичный DDL, который
на SQLite не выполняется (`ALTER TABLE ... ALTER COLUMN ... SET NOT NULL`
и подобное — подтверждено отдельной, неудачной попыткой на SQLite в этой
же сессии, ожидаемо).

Дополнительно, специально для новой миграции:
- `alembic downgrade -1` — откатывает ровно `contract_versions`, `current`
  после — `201286e2acd0` (предыдущий head).
- `alembic upgrade head` повторно — переприменяется чисто, `current` —
  снова `c13606d92787`.
- `\d contract_versions` в psql — структура таблицы (колонки, типы,
  индексы, `UNIQUE`/`CHECK`-constraint'ы, FK на `projects`/`users`,
  отсутствие FK на `contracts`) подтверждена вручную, совпадает с
  ожидаемой из миграции.

Одноразовый Postgres-контейнер и сеть удалены сразу после проверки, ничего
не осталось.

### 9.5. Secrets scan

Выполнен по фактическому diff'у (включая новую миграцию, модель, все
8 trip-wire-файлов), тем же методом, что в предыдущих пяти областях.
Два совпадения по regex — оба ложноположительные (`ai_secretary`-импорт
из-за подстроки "secret" в "secretary"; имя тестовой функции
`test_runtime_orchestrator_never_publishes_captured_output_or_secrets`,
которая сама ПРОВЕРЯЕТ отсутствие утечки секретов, не содержит их).
Высокоэнтропийных строк — только git-заголовки диффа (пути), не значения.
**Чисто.**

### 9.6. Ограничения — соблюдены

Коллизирующие миграции (2.1) не перенесены — задокументированы отдельно
как альтернатива для MVP-3. Code-less миграции (2.2, кроме `contract_versions`)
не перенесены. Constraint-relax на `v54_provider_actions` не тронут.
Существующая цепочка main не переупорядочена, не сквошена — новая
миграция строго добавлена поверх текущего head. Никакого API/бизнес-кода,
пишущего в `contract_versions`, не добавлено — только схема+модель.
Пункты 1-2 (отложенные) не "дозакрыты" в этой сессии — только
задокументированы отдельным файлом по прямому указанию.

Коммит по итогам п. 9 ещё не сделан — ждёт подтверждения пользователя.
