# MVP-1 Main Integration — App Composition Preflight

Дата: 2026-09-11

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед App composition: `92626550f279807b57ddbb5ed831bd592c9057c2`
(Storage adapters, закрыта и запушена).

Этот документ фиксирует результат read-only анализа слоя app composition
перед selective port из `codex/mvp1-phase2-review` (HEAD `4cd0a62`) в `main`,
по той же схеме, что и
[`mvp1-main-integration-snapshots-preflight.md`](mvp1-main-integration-snapshots-preflight.md),
[`mvp1-main-integration-ocr-preflight.md`](mvp1-main-integration-ocr-preflight.md)
и [`mvp1-main-integration-storage-adapters-preflight.md`](mvp1-main-integration-storage-adapters-preflight.md).
Он является планом следующей сессии. **Код не менялся, коммит не делался.**

## 1. Что такое `app_mvp1.py` и зачем он существует

`backend/app/app_mvp1.py` (81 строка, review-only, отсутствует на main
целиком) — это **отдельный, самостоятельный ASGI-модуль**
(`uvicorn app.app_mvp1:app`), а не альтернативная конфигурация основного
`app/main.py`. Его цель по докстрингу: "этот модуль намеренно не
импортирует `app.main` или агрегатный пакет `app.models`, чтобы более
поздние MVP-роутеры и model-бандлы не инициализировались как побочный
эффект".

Практически это значит: изолированная композиция, которая выставляет
**только** MVP-1-ядро (identity, tenants/projects, source references,
snapshots, virtual documents, proposals/change-batches, rules, rollback,
audit) и **гарантированно** не импортирует ни один более поздний
MVP2+/v54-модуль — ни транзитивно через модели, ни через бизнес-логику.
Это доказывается единственным существующим тестом
(`test_mvp1_phase1b_isolation.py`, разобран в п. 4) через subprocess-пробу
`sys.modules` после `from app.app_mvp1 import app`.

**Важно: `app_mvp1.py` нигде не задействован в деплое** — ни в
CI/CD-конфигах, ни в Dockerfile/docker-compose, ни на main, ни на review
(`grep` по обеим веткам пуст). Это самостоятельная, но не подключённая к
пайплайну возможность — снижает срочность, но не снимает вопрос "нужна ли
она вообще" с повестки этого preflight.

## 2. Метод

Сравнил main vs review по: `app_mvp1.py` (целиком новый), `main.py` (обе
ветки имеют полную композицию, diff есть), `pilot_composition.py`
(идентичен byte-for-byte, не трогать), `models/__init__.py` (структурная
развилка), `core/readiness.py`/`core/mvp1_readiness.py`, `mvp1_extensions.py`
(целиком новый), и часть `organizer.py`, которая относится к router
registration (`resources_router`) — **не** к managed-copy/archived-project
логике, которая уже решена в Storage adapters (см. п. 3.6, важное
пересечение).

## 3. Архитектурные развилки

### 3.1. Полный список того, чего не хватает на main для рабочего `app_mvp1.py`

Прямой перенос одного файла `app_mvp1.py` **не запустится** на main — он
импортирует четыре вещи, которых на main нет вообще:

| Импорт в `app_mvp1.py` | Статус на main |
|---|---|
| `from app.mvp1_extensions import ...` (косвенно, через `organizer.py`) | Файла нет |
| `from app.core.mvp1_readiness import readiness_report` | Файла нет |
| `from app.organizer import resources_router, router as organizer_router` | `resources_router` не существует в `organizer.py` |
| `os.environ["PU_MODEL_SCOPE"] = "mvp1"` + условные импорты в `models/__init__.py` | Механизма нет — `models/__init__.py` безусловен |

Каждый из четырёх разобран отдельно ниже (3.2–3.5).

### 3.2. `mvp1_extensions.py` — новый файл, реальный, механический порт

Назначение: bridge-слой, позволяющий **одному и тому же** `organizer.py`
корректно работать и в полном приложении (где `create_tasks_from_files`/
`create_response_drafts`/`create_governance_items`/`notify_telegram`
вызываются как сегодня), и в изолированном MVP-1 (где эти вызовы должны
быть no-op, не импортируя `task_engine`/`response_engine`/
`governance_engine`/`app.integrations.telegram` вовсе).

```python
def enabled() -> bool:
    configured = os.getenv("PU_MVP1_OPTIONAL_EXTENSIONS")
    if configured is None:
        return os.getenv("PU_MODEL_SCOPE", "").strip().lower() != "mvp1"
    return configured.strip().lower() in {"1", "true", "yes", "on"}
```

Дефолт (`PU_MVP1_OPTIONAL_EXTENSIONS` не задан) — включено, если
`PU_MODEL_SCOPE` не `"mvp1"`. То есть **для main без изменения окружения
поведение не меняется**: `enabled()` возвращает `True`, `run_post_analysis`
делает ровно то, что сегодня прямые вызовы делают.

Сверено сигнатурами: main's `organizer.py` сегодня вызывает
`create_tasks_from_files(db, project_id, session_id, copy_items)`,
`create_response_drafts(db, project_id, session_id, copy_items)`,
`create_governance_items(db, project_id, copy_items)` — review's
`run_post_analysis(db, project_id, session_id, files, *, source_type=None)`
вызывает те же три функции с теми же позиционными аргументами
(`source_type` — опциональный kwarg, backward-compatible). **Механический
порт**, поведение полного приложения не меняется при дефолтных env vars.

### 3.3. `core/mvp1_readiness.py` vs `core/readiness.py` — реальная, обоснованная развилка

`core/readiness.py` (118 строк) **идентичен byte-for-byte** на main и
review (`diff` пуст) — не трогать, не развилка сам по себе. Но он
**непригоден** для изолированного MVP-1-приложения: импортирует
`app.automations.gmail`/`app.automations.ai_secretary` (транзитивно тянут
`GoogleOAuthToken`-обвязку и, через `app.automation_engine`, вероятно
`AutomationRule`/`Message` — later-MVP модели), плюс проверяет
`gmail_automation`/`ai_secretary_automation`/`local_ocr`/`telegram` —
concerns, которых у MVP-1-ядра просто нет.

Review's `core/mvp1_readiness.py` (38 строк, самостоятельный, review-only)
— узкий поднабор: `app_secret`/`bootstrap_token`/`token_encryption`/
`database`/`schema`, без единого later-MVP импорта. Это ровно то, что
`test_mvp1_composition_does_not_import_later_mvp_modules` требует
(запуск `readiness()`-эндпоинта `app_mvp1.py` не должен тянуть
`app.automations.*`).

**Решение: хороший кандидат, новый самодостаточный файл, не конфликтует с
`core/readiness.py`.**

### 3.4. `models/__init__.py` — концепция переносима, но список review устарел

Review оборачивает "поздние" импорты в `if not MVP1_MODEL_SCOPE:` (флаг из
`PU_MODEL_SCOPE` env var), оставляя MVP-1-ядро безусловным. Механизм сам по
себе разумный и не конфликтует с main (main никогда не устанавливает
`PU_MODEL_SCOPE`, значит `MVP1_MODEL_SCOPE=False` всегда, поведение полного
приложения не меняется).

**Но список моделей внутри `if not MVP1_MODEL_SCOPE:` на review — не
актуален относительно main.** Main успел добавить множество моделей после
разветвления, которых нет в review's "поздний" список вообще:
`ContractVersion`, `GovernanceHistory`, `CashFlowFactHistory`,
`ObligationHistory`, `ProjectContactHistory`, `GmailHistoryCheckpoint`/
`GmailHistoryCheckpointEvent`, `MailUserSettings`, `ContactConflict`
(на `ProjectContact`), модели `app.models.search`, `app.mvp4.supply.models`,
`app.models.management_digest`, `app.models.meeting_source_binding` — и,
отдельно, main's `models/__init__.py` **не содержит** импортов из
`app.mvp4.supply.models`/`app.models.search`/`app.models.management_digest`/
`app.models.meeting_source_binding` вовсе (эти вообще отсутствуют на main
как модули — см. п. 3.7, той же категории, что и `storage_mutations`).

Буквальный копипаст review's блока на main:
1. Пропустит несколько десятков реально существующих на main "поздних"
   моделей (они остались бы в безусловном верхнем блоке или вообще не
   упомянуты — то есть либо всегда грузятся, либо ломают `__all__`).
2. Попытается импортировать 4 модуля, которых на main не существует
   (`ImportError`).

**Решение: концепция (условный импорт по `PU_MODEL_SCOPE`) переносима и
разумна, но список "поздних" моделей должен быть заново составлен по
актуальному `models/__init__.py` main, а не скопирован с review.** Не
тривиальный порт.

**Дополнительный, не решённый здесь вопрос:** нужно проверить, не ссылается
ли ни одна MVP-1-ядро модель (например, `Document`, `VirtualNode`,
`OrganizerSession`) через SQLAlchemy `relationship()` на модель из "позднего"
списка — если такая связь есть, условное исключение импорта сломает
маппер (`InvalidRequestError: expression ... failed to locate a name`) даже
для MVP-1-ядра эндпоинтов. Не проверял построчно — это верификация,
обязательная перед реализацией, не сделанная в этом preflight.

### 3.5. `organizer.py`'s `resources_router` — реальный, низкорисковый, self-contained кандидат

Отдельный `APIRouter(tags=["mvp1-resources"])` **без префикса** (в отличие
от `router = APIRouter(prefix="/organizer", ...)`), который **дублирует**
регистрацию уже существующих handler-функций под другими, prefix-less
путями через стек декораторов на той же функции:

```python
@resources_router.get("/proposals")
@resources_router.get("/change-batches")
@router.get("/proposals")
def proposals(...): ...
```

Полный список пар (review's `organizer.py`, дублирующих alias'ов, не новых
функций): `/proposals` ↔ `/organizer/proposals`, `/proposals/{id}` ↔
`/organizer/proposals/{id}`, `/change-batches/{id}/apply` ↔
`/organizer/proposals/{id}/apply`, `/rollbacks/{id}` и
`/change-batches/{id}/rollback` ↔ `/organizer/proposals/{id}/rollback`,
`/rules` (POST) ↔ `/organizer/rules`.

**Важно: это не MVP-1-специфичная вещь.** Review's diff показывает, что
`resources_router` регистрируется **и в полном `main.py`**
(`app.include_router(mvp1_resources_router, dependencies=[Depends(require_user)])`),
не только в `app_mvp1.py` — то есть это решение про **общую номенклатуру
API** ("change-batches" как каноническое внешнее имя для "proposals"),
independent от изоляции MVP-1.

Реализация — чистое добавление декораторов и одного `APIRouter()` объекта,
**без изменения** логики самих handler-функций. Самый низкий риск из всей
области.

**Решение: хороший кандидат, самодостаточен, применим к main независимо от
решения по `app_mvp1.py`/`PU_MODEL_SCOPE`.**

### 3.6. `organizer.py`'s `managed_copy_key`/capability-gate/`archived_at` — НЕ app composition, уже решено/отложено в другом месте

Review's полный diff `organizer.py` смешивает **три** несвязанных темы в
одном файле (ровно как `drive.py` в Storage adapters preflight смешивал
5 тем). Разбираю, чтобы не спутать при реализации:

1. `notify_telegram` → редирект на `mvp1_extensions.notify`, прямые вызовы
   `create_tasks_from_files`/`create_response_drafts`/
   `create_governance_items` → `run_post_analysis` — **это app composition**,
   разобрано в 3.2.
2. `_scan_worker` получает параметр `managed_copy_key: str | None = None` и
   capability-gate (`if managed_copy_key and not
   getattr(drive, "supports_managed_copy_idempotency", False): raise
   ValueError("managed_copy_reconciliation_unavailable")`) —
   **это НЕ app composition.** Это та же тема, что уже была решена в
   [Storage adapters](mvp1-main-integration-storage-adapters-preflight.md)
   п. 2.4.d/11.1: там explicit выбран вариант (а) — стабильный
   `idempotency_key` вычисляется **внутри** `_scan_worker` из `session_id`
   (`f"organizer-session-{session_id}"`), **без** отдельного параметра
   функции и **без** capability-gate. Review's `managed_copy_key`-параметр —
   часть более широкого design'а с `organizer_engine/managed_copies.py`,
   который был **явно отклонён** в пользу минимального варианта. Порт этого
   куска при реализации App composition **отменил бы уже принятое и
   закоммиченное (`9262655`) решение** — явно исключить.
3. `if not project or project.get("archived_at") is not None: raise
   ValueError("Project not found")` — новый guard, блокирующий
   `_scan_worker` для архивированного проекта. Main's
   `OrganizerRepository.project()` сегодня делает
   `SELECT id,name FROM projects WHERE id=:id` — не выбирает `archived_at`
   вовсе, так что `project.get("archived_at")` на main всегда `None`
   (ключа просто нет в `Mapping`), проверка никогда не сработает — нужна
   правка самого SQL-запроса, а не только guard-строки. По смыслу это часть
   project-archival lifecycle, той же темы, что
   `organizer_engine/managed_copies.py::run_managed_copy_cleanup`,
   зафиксированной как **отдельная будущая область** в Storage adapters
   preflight п. 3/11.4 ("Managed-copy lifecycle (project-archival cleanup)"),
   **не app composition**.

**Решение: при реализации App composition трогать в `organizer.py` только
пункт 1 (`mvp1_extensions`-редирект) и `resources_router` (3.5). Пункты 2 и
3 — явно не переносить здесь, они принадлежат другим, уже
решённым/отложенным темам.**

### 3.7. `main.py` (полная композиция) — main строго впереди в part, плюс отдельные крупные непортированные фичи

Diff `main.py` (223 vs 220 строк) раскладывается на:

**main строго впереди (не переносить, review устарел):**
- Убрана CI-специфичная обвязка lifespan'а
  (`install_ci_local_upload_runtime`/`configure_local_upload_runtime`) —
  введена коммитом `1f88b62` ("fix(ci): enable isolated encrypted upload
  smoke", 2026-09-05), **не предок** review-ветки. Review's упрощённый
  lifespan ("Keeping API startup side-effect free...") — не эволюция этого
  кода, а версия, которая никогда его не видела.
- `Permissions-Policy: geolocation=(self)` — введено `f1af5a9` ("feat(projects):
  add site GPS location", 2026-09-04), тоже **не предок** review. Review's
  `geolocation=()` — откат/незнание фичи, не hardening.
- `app.api.mail` (`mail_router`) — **отсутствует на review вообще**
  (не удалён, никогда не существовал на этой ветке) — main-only фича
  (`c4aa47b feat(mail): add provider-neutral mail client backend`). Нечего
  переносить, направление обратное: это main опережает review, а не
  наоборот.

**Крупные, полностью непортированные feature-области (НЕ app composition,
адресуются отдельно, если вообще адресуются):**

| Router (review-only) | Модуль | На main |
|---|---|---|
| `execution_forecast_router` | `app.execution_forecast.api` (5 файлов) | Нет |
| `supply_router` | `app.mvp4.supply.router` (5 файлов) | Нет |
| `search_router` | `app.api.search` | Нет |
| `provider_actions_router` | `app.api.provider_actions` | Нет |
| `storage_mutations_router` | `app.api.storage_mutations` | Нет — **та же подсистема**, что уже зафиксирована как отдельная будущая область в [Storage adapters preflight](mvp1-main-integration-storage-adapters-preflight.md) п. 3 |

Это не "различия в router registration" в смысле composition-механики — это
целые непостроенные на main продуктовые области (прогноз исполнения,
снабжение/MVP-4, поиск, provider actions execution). Регистрация роутера —
последний шаг; сначала должны существовать сами модули, модели, тесты —
контракт, реализация, гейты, каждый — отдельная будущая веха. **Не
рассматривать как часть App composition.**

**Низкорисковый, самостоятельный кандидат (пересекается с 3.5):**
- `app.include_router(mvp1_resources_router, dependencies=[Depends(require_user)])`
  в полном `main.py` — тот же `resources_router` из 3.5, регистрация в
  полном приложении. Не требует ничего сверх 3.5.

## 4. Обязательная матрица проверок

Review's тестовое покрытие этой области — **один файл**,
`test_mvp1_phase1b_isolation.py` (5 тестов, все через subprocess-пробу
`sys.modules`/`app.routes`, offline, без реальной БД/сети).

| Инвариант | Тест | Состояние |
|---|---|---|
| `app_mvp1.py` не импортирует ни один later-MVP модуль/модель транзитивно | `test_mvp1_composition_does_not_import_later_mvp_modules` | Портируется **с адаптацией**: banned-список (`app.task_engine`, `app.models.task`, ...) сверен только с review's текущим составом later-MVP модулей; main имеет дополнительные later-MVP-модули, которых нет на review (`app.automations.gmail`, `app.automations.ai_secretary`, `app.mail_engine`/`app.api.mail`, v54/pilot-обвязка, и т.д.) — список нужно расширить под фактический граф main, не копировать |
| `app_mvp1.py` выставляет только канонические MVP-1-ресурсы, ничего из later-MVP | `test_mvp1_composition_exposes_canonical_phase1_resources_only` | Портируется с той же адаптацией — плюс нужно решить, входит ли `resources_router`'s alias-набор (`/change-batches`, `/rollbacks/{id}`) в "канонический" набор для main (да, по смыслу 3.5) |
| Post-analysis bridge — explicit no-op при отсутствии extensions | `test_mvp1_post_analysis_bridge_is_an_explicit_noop` | Портируется без адаптации, если 3.2 перенесён как есть |
| MVP-1 readiness остаётся fail-closed без конфигурации | `test_mvp1_readiness_remains_fail_closed_without_configuration` | Портируется без адаптации, если 3.3 перенесён как есть |
| Именованные MVP-1-ядро файлы не имеют eager later-MVP импортов на уровне текста модуля | `test_named_mvp1_core_files_have_no_eager_later_mvp_imports` | Портируется с адаптацией: список проверяемых файлов (`app/api/projects.py`, `app/api/documents.py`, `app/api/workspace.py`, `app/organizer.py`) и список запрещённых `from`-паттернов нужно сверить с main — main's `workspace.py`/`organizer.py` могли получить новые импорты после разветвления (например, `app.ocr_quality.xlsx_cells` — не запрещённый, но нужно перепроверить весь список текущих импортов этих файлов на предмет случайных later-MVP связей) |
| `models/__init__.py`'s `PU_MODEL_SCOPE=mvp1` не ломает SQLAlchemy mapper configuration для MVP-1-ядра моделей | Тест отсутствует на обеих ветках | Обязательный новый тест, если 3.4 реализуется — должен явно сконфигурировать все mapper'ы (`configure_mappers()`) под `PU_MODEL_SCOPE=mvp1` и проверить отсутствие `InvalidRequestError` от неразрешённых relationship-ссылок |
| `resources_router` alias'ы отдают тот же payload, что исходные `/organizer/...`-пути | Тест отсутствует на обеих ветках (review's тест проверяет только присутствие путей в `app.routes`, не эквивалентность ответов) | Обязательный новый тест, если 3.5 реализуется |
| Полное приложение (`main.py`) не регрессирует при добавлении `mvp1_resources_router`/`mvp1_extensions`-редиректа | Существующий regression-набор (`test_storage_binding_validation.py`, `test_job_hardening_contract.py`, и т.д. — уже используются как regression-guard в предыдущих трёх областях) | Regression-guard, обязателен при любой правке `organizer.py`/`main.py` |
| `managed_copy_key`/capability-gate/`archived_at`-guard **не** просачиваются в это изменение | Нет отдельного теста — проверяется вручную по diff'у при реализации (см. 3.6) | Explicit проверка перед коммитом, не автоматический гейт |

## 5. Уровни доказательства

### Offline / synthetic

Весь review's `test_mvp1_phase1b_isolation.py` — чистый offline: каждый
тест запускает `python -c "..."` в subprocess с `DATABASE_URL=sqlite+pysqlite:///:memory:`
и без секретов в env (явно вычищены `APP_SECRET_KEY`/`BOOTSTRAP_TOKEN`/
`TOKEN_ENCRYPTION_KEY`/`PU_MODEL_SCOPE`/`PU_MVP1_OPTIONAL_EXTENSIONS` из
окружения), проверяя `sys.modules`/`app.routes`/return-значения по чистому
импорту. Ни разу не поднимает реальный сервер, не делает сетевых вызовов.
Новый тест на mapper-конфигурацию (4) тоже чисто offline —
`configure_mappers()` не требует реальной БД, только валидный Python-граф
моделей.

### Tested runtime / PostgreSQL

**Не требуется.** Вся область — Python-level import-graph и route-
registration; ни одного нового DB-запроса, ни новой миграции, ни
DB-конкурентности. `readiness_report()` (обе версии) делает `SELECT 1`/
`SELECT version_num FROM alembic_version` — но это уже существующий,
идентичный на обеих ветках код (п. 3.3), не новая логика.

## 6. Alembic

**Новых миграций не требуется.** `models/__init__.py`'s условные импорты —
чисто Python-уровня (какие классы регистрируются в `Base.metadata` при
импорте пакета), не меняют ни одну схему. Проверено: ни `backend/migrations/env.py`,
ни main, ни review нигде не устанавливают `PU_MODEL_SCOPE` — значит
Alembic (запускаемый без этой переменной) всегда видит полный набор
моделей, включая условный блок, как и раньше. Текущий head после Storage
adapters (`9262655`, не менял миграций) — без изменений от предыдущей
области.

## 7. Secrets scan

Код не менялся, коммита нет — scan не выполнялся. Требуется по полному diff
перед будущим app-composition-коммитом, тем же методом, что в
Snapshots/OCR/Storage adapters.

## 8. Предполагаемый состав будущего коммита

Фактически изменено `0` файлов. Ожидаемый минимальный набор (уточнится по
факту реализации, особенно по итогам верификации в 3.4):

- `backend/app/mvp1_extensions.py` — новый файл, порт без адаптации (3.2).
- `backend/app/core/mvp1_readiness.py` — новый файл, порт без адаптации (3.3).
- `backend/app/models/__init__.py` — точечно: добавить `PU_MODEL_SCOPE`
  условный блок, но со списком моделей, заново составленным по текущему
  состоянию main, не скопированным с review (3.4) — плюс верификация
  relationship-графа перед этим.
- `backend/app/organizer.py` — точечно: `notify_telegram`-редирект на
  `mvp1_extensions.notify`, `run_post_analysis`-обвязка, новый
  `resources_router` с alias-декораторами (3.2 + 3.5). **Явно не
  трогать** `managed_copy_key`/capability-gate/`archived_at` (3.6).
- `backend/app/main.py` — точечно: `app.include_router(mvp1_resources_router,
  dependencies=[Depends(require_user)])` (3.5/3.7). Не трогать
  lifespan/CSP-заголовки/несуществующие роутеры (3.7).
- `backend/app/app_mvp1.py` — новый файл, порт с адаптацией под актуальные
  импорты main (routers, которых на review нет и наоборот).
- `backend/tests/test_mvp1_phase1b_isolation.py` — новый, порт с
  адаптацией banned-module/canonical-paths списков под main (п. 4).
- Новый тест на mapper-конфигурацию под `PU_MODEL_SCOPE=mvp1` — с нуля, гап
  из п. 4.
- Новый тест на эквивалентность `resources_router`-алиасов исходным путям —
  с нуля, гап из п. 4.
- Итоговый audit области (`mvp1-main-integration-app-composition-completion.md`).

**НЕ входит**: `pilot_composition.py` (идентичен, не трогать),
`core/readiness.py` (идентичен, не трогать), lifespan CI-обвязка/CSP на
main (main строго впереди), весь `mail_router` (main-only, не трогать),
`execution_forecast`/`mvp4.supply`/`search`/`provider_actions`/
`storage_mutations` роутеры (отдельные, непостроенные feature-области, не
app composition), `managed_copy_key`/capability-gate/`archived_at`-guard в
`organizer.py` (уже решено иначе в Storage adapters / отдельная будущая
область project-archival lifecycle).

Это планируемый, а не финальный список.

## 9. Ограничения на реализацию

- `main.py` **не заменять** целиком — только точечное добавление
  `mvp1_resources_router` (3.5/3.7); lifespan, CSP-заголовки, весь
  существующий router-набор main остаются нетронутыми.
- `organizer.py` **не заменять** целиком — только `mvp1_extensions`-редирект
  и `resources_router` (3.2 + 3.5). Managed-copy/archived-project куски из
  review's diff (3.6) исключить явно — их перенос отменил бы уже
  закоммиченное решение Storage adapters (`9262655`) без отдельного
  решения пользователя.
- `models/__init__.py` — список "поздних" моделей должен быть заново
  составлен по фактическому main, не скопирован с review (3.4); перед
  реализацией — верифицировать отсутствие relationship-ссылок из
  MVP-1-ядра моделей на "поздние" модели.
- `pilot_composition.py`, `core/readiness.py` — не трогать, идентичны,
  подтверждено.
- Роутеры для `execution_forecast`/`mvp4.supply`/`search`/
  `provider_actions`/`storage_mutations` — не переносить ни в каком виде;
  это отдельные, непостроенные продуктовые области, не app composition.
- Решение по 3.4 (список моделей, verification relationship-графа) должно
  быть принято явно до кода — как и в предыдущих трёх областях.
- Любое отклонение от этих ограничений — описать до коммита, как и в
  Snapshots/OCR/Storage adapters.

## 10. Git-состояние на preflight

```text
git diff --check: PASS
git status --porcelain: clean
```

Код не менялся, коммит не делался. Следующая сессия должна начать с этого
документа, в первую очередь утвердить: нужен ли `app_mvp1.py` вообще (он
нигде не задеплоен ни на одной ветке — стоит явно решить целевое
назначение перед портом, не просто "потому что есть в review"), затем
решения по 3.4 (модели) и объём переноса (полный `app_mvp1.py` vs только
самостоятельные куски 3.2/3.3/3.5, независимо полезные для main без
изоляции), пройти гейты: offline regression, PostgreSQL tested-runtime (не
требуется, см. п. 5), secrets scan, итоговый audit.

## 11. Резолюция — implementation log (эта сессия)

**Scope этой сессии — только п. 3.5 (`resources_router`), явно НЕ весь
план п. 8.**

### 11.1. Принятое решение (зафиксировано до кода)

Пользователь прямо решил: `app_mvp1.py` **не портируется** как отдельный
ASGI-модуль в main. Обоснование, зафиксированное явно: на review это был
**доказательный артефакт изоляции** ("докажем, что MVP-1-ядро можно
собрать отдельно и оно не тянет позднюю логику"), а не эксплуатационная
единица — нигде не задеплоен ни на одной из веток (подтверждено в п. 1
этого документа: отсутствует в CI/CD, Dockerfile, docker-compose на обеих
ветках). Раз он не эксплуатируется, самостоятельной пользовательской
ценности в его порте нет.

Как прямое следствие — **не переносятся**:
- `mvp1_extensions.py` (3.2) — существует только как bridge, обслуживающий
  изолированную композицию; без `app_mvp1.py` у него нет потребителя.
- `core/mvp1_readiness.py` (3.3) — существует только потому, что
  `core/readiness.py` непригоден для изолированной композиции; без
  `app_mvp1.py` эта причина отпадает, `core/readiness.py` продолжает
  обслуживать `main.py` как и сегодня.
- `models/__init__.py`'s `PU_MODEL_SCOPE`-фильтрация (3.4) — единственный
  потребитель флага — изолированная композиция; без неё это мёртвый
  механизм, а её список моделей и без того был признан устаревшим
  относительно main (см. 3.4) — лишний повод не делать сейчас работу,
  которая тут же становится ненужной.

**Единственное, что реализовано — п. 3.5, `resources_router`.** Это была
единственная часть, явно отмеченная как имеющая самостоятельную
пользовательскую ценность независимо от вопроса про `app_mvp1.py`: URL-
алиасинг `/proposals`↔`/change-batches`, `/proposals/{id}`↔
`/change-batches/{id}`, `/proposals/{id}/apply`↔
`/change-batches/{id}/apply`, `/proposals/{id}/rollback`↔
`/rollbacks/{id}`↔`/change-batches/{id}/rollback`, `/rules`↔`/rules` —
регистрируется и в `organizer.py` (новый router), и в `main.py`
(`app.include_router(mvp1_resources_router, dependencies=[Depends(require_user)])`),
как это уже сделано на review's полном `main.py`.

### 11.2. Фактически изменённые/новые файлы

- `backend/app/organizer.py` — точечно: новый
  `resources_router = APIRouter(tags=["mvp1-resources"])` сразу после
  существующего `router`; декораторы `@resources_router.get/post(...)`
  добавлены поверх 5 уже существующих handler-функций
  (`proposals`, `proposal`, `apply`, `rollback`, `create_rule`) — **без
  единого изменения их тела**. `mvp1_extensions`-редирект, managed-copy/
  archived-project куски (3.2/3.6) — не тронуты, как решено в 11.1.
- `backend/app/main.py` — точечно: импорт `resources_router as
  mvp1_resources_router` из `app.organizer`, одна строка
  `app.include_router(mvp1_resources_router, dependencies=[Depends(require_user)])`
  сразу после `organizer_router`. Lifespan, CSP-заголовки, остальной
  router-набор не тронуты.
- `backend/tests/test_proposal_review_api.py` — 2 новых теста:
  `test_resources_router_exposes_exactly_the_change_batches_aliases`
  (полный набор путей `resources_router`, ни больше ни меньше) и
  `test_resources_router_aliases_are_the_same_handlers_as_organizer_router`
  — доказывает, что каждый alias — **тот же самый Python-объект функции**,
  что и канонический `/organizer/...`-маршрут (не переimplementация),
  закрывает пробел из матрицы п. 4 ("resources_router alias'ы отдают тот
  же payload") сильнее, чем требовалось: не просто эквивалентность ответа
  в рантайме, а структурная невозможность разойтись.

### 11.3. Offline / synthetic regression — фактические числа

Тот же образ (`app-backend:cf06cf21544a428ab13876f1273c7ca53128fe9d`),
эфемерный запуск, `--network none`, `DATABASE_URL=sqlite:///:memory:`.

- `test_proposal_review_api.py` (7, включая 2 новых) — **PASS**.
- `test_proposal_review_api.py` + `test_drive_safety.py` +
  `test_managed_copy_key_wiring.py` + `test_storage_binding_validation.py` +
  `test_job_hardening_contract.py` вместе — **64 passed**, 65.02s.
- Дополнительно: `from app.main import app` — импортируется без ошибок
  (`app.title == "PU Workspace"`); `resources_router.routes` — ровно 8
  маршрутов, точно совпадает с ожидаемым набором (проверено вручную и
  тестом 11.2). Полную итерацию `app.main.app.routes` через `{route.path
  for route in app.routes}` выполнить нельзя — это тот же pre-existing
  `_IncludedRouter`-дефект окружения (см. п. 5 ниже), воспроизведён
  повторно на чистом `git stash` (без изменений этой сессии) — не новый,
  не вызван `resources_router`.
- Полный `backend/tests/`: **1545 passed** (было 1543 после Storage
  adapters; +2 — ровно новые тесты этой сессии), 25 skipped, тот же 1
  pre-existing failure, 528.40s.

### 11.4. PostgreSQL tested runtime

**Не требуется.** Изменение — регистрация уже существующих handler-функций
под дополнительными путями (декоратор поверх декоратора) плюс одна строка
`include_router` в `main.py`. Ни одного нового SQL-запроса, ни новой
модели, ни изменения существующей бизнес-логики — каждый alias вызывает
ровно тот же код, что и сегодняшний канонический путь (структурно
гарантировано тестом на идентичность объекта функции, 11.2). Новых
Alembic-миграций нет.

### 11.5. Secrets scan

Выполнен по фактическому diff'у (`organizer.py`, `main.py`,
`test_proposal_review_api.py`), тем же методом, что в предыдущих трёх
областях: grep по паттернам ключей/токенов и по высокоэнтропийным
base64-подобным строкам ≥32 символов. Совпадений нет. **Чисто.**

### 11.6. Ограничения — соблюдены

`app_mvp1.py`, `mvp1_extensions.py`, `core/mvp1_readiness.py`,
`models/__init__.py`'s `PU_MODEL_SCOPE`-фильтрация — не перенесены,
решение зафиксировано явно (11.1). `organizer.py`/`main.py` не заменены
целиком. Managed-copy/archived-project/`execution_forecast`/`mvp4.supply`/
`search`/`provider_actions`/`storage_mutations` — не тронуты, как и
решено в п. 9.

Коммит по итогам п. 11 ещё не сделан — ждёт финального прогона полного
regression и подтверждения пользователя.
