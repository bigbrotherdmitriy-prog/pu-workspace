# HANDOFF — PU Workspace → Claude

Дата: 03.11.2026. Это передача фактического состояния, **не акт завершения плана и не разрешение production-деплоя**. Файл подготовлен по коду, локальному Git, конфигам и сохранённым результатам. Секреты не читались и не включены.

## 1. Проект и действующая постановка

PU Workspace — веб-приложение для подрядчика и команды строительного проекта: документы и договоры, сметы, журнал фактических работ, КС-2/КС-3, согласование с заказчиком, замечания/фото, закупки, чертежи, управленческие задачи и финансы. Есть AI-помощники и интеграции с почтой/хранилищами. Исходный README описывает более ранний organizer MVP и не отражает весь текущий объём.

Основной пользовательский документ: [MASTER-PLAN-2026-10-30.md](MASTER-PLAN-2026-10-30.md). Checklist 30.10 — приёмка, старое ТЗ 13.10 его не заменяет. План рассчитан ориентировочно на 60 рабочих дней, **это оценки, не обещанные даты**.

Главный бизнес-инвариант: **КС только из факта журнала, цены из сметы, превышение доступного объёма блокируется**. Физический приход материала не выводится из оплаты/заказанного количества; расход материала не равен автоматически объёму работы.

Прочитать также:

- [AGENTS.md](AGENTS.md) — права, роли и исключение резервного исполнителя.
- [docs/WORK-STATUS-2026-10-30.md](docs/WORK-STATUS-2026-10-30.md) — хронология, свежие записи сверху.
- [docs/ACCEPTANCE-MATRIX-2026-10-30.md](docs/ACCEPTANCE-MATRIX-2026-10-30.md), [docs/ACCEPTANCE-STATUS-2026-10-31.md](docs/ACCEPTANCE-STATUS-2026-10-31.md).
- `.pu-autonomy/control.json`, `.pu-autonomy/OPERATING-RULES.md`, `.pu-autonomy/evidence/` — очередь и доказательства. Старые поля/записи иногда устарели; учитывать более свежую конкретную проверку.

## 2. Стек и архитектура

| Слой | Реализация и входные точки |
|---|---|
| Backend | Python 3.12; FastAPI 0.141.1, Starlette 1.3.1, Uvicorn 0.35.0. `backend/app/main.py` собирает routers, lifespan и static frontend. |
| Данные | SQLAlchemy 2.0.43, psycopg 3.2.9, PostgreSQL 16; Alembic 1.16.5. `backend/app/database.py`, `backend/app/models/`, `backend/migrations/versions/`, `backend/app/schema.py`. |
| UI | TypeScript 5.9.3, React 19, Vite 7.3.7; `frontend/src/App.tsx`, `frontend/src/modules/`. Точный resolved dependency set — `frontend/pnpm-lock.yaml`. |
| Тесты | pytest 9.1.1; Vitest 4.1.12 + jsdom; Playwright 1.58.2 для E2E. |
| Документы | openpyxl, xlrd, pypdf, Pillow; OCR/конвертация через Tesseract, Poppler, antiword; JRE/MPXJ/JPype для планов. PDF-прототипы используют также ReportLab, см. риск зависимостей ниже. |
| Фоновые задания | `backend/app/jobs/worker.py`, scheduler; Compose включает API, DB, worker, scheduler. Для durable-режима используется `PU_BACKGROUND_EXECUTION`; не путать с очередью разработки DeepSeek. |
| Инфраструктура | `backend/Dockerfile`, `docker-compose.yml`, отдельный `infra/primary/docker-compose.yml`, `.github/workflows/`. Android-клиент — `android/`, PWA/offline — `frontend/src/offline/`. |

Структура и связи:

- `backend/app/api/` — HTTP DTO, аутентификация и role guards, вызов сервисов. Auth/CSRF: `backend/app/core/auth.py`, `api/auth.py`; membership/tenant-модели — `models/project_member.py`, `models/workspace_tenant.py`.
- `estimates.py`, `estimate_import.py`, `work_log.py`, `ks_forms.py`, `acceptance_acts.py` и одноимённые API — цепочка смета → факт → акт. Импортёр — `backend/app/estimate_import.py`, тесты — `backend/tests/test_estimate_import.py`.
- `customer_portal.py`, `api/customer.py` — кабинет/решения заказчика; `defects.py`, `api/defects.py` — замечания и фото; `rd.py`, `purchase.py`, `equipment.py` — чертежи, закупки, оборудование.
- `api/mobile_sync.py`, `models/mobile_sync.py`, `frontend/src/offline/{db,syncEngine,types,useOfflineSync}.ts` — локальная IndexedDB-очередь и серверные receipts/conflicts. Сейчас она не реализует offline-создание журнала/замечаний.
- `integrations/`, `api/google_drive.py`, `api/gmail.py`, Yandex API, `gemini_analysis.py`, AI policy/secretary — провайдеры и анализ. `source_evidence/`, `staging/`, V54-модули — источник, версия, доказательства и жизненный цикл материализаций.
- `backend/app/react_dist/` — собранная статика, **не исходники UI**. Vite по умолчанию очищает этот каталог при сборке; для review использовать отдельный `--outDir`.
- `docs/operations/` — локальные source-capsules, patches, JSON результатов, diagnostic tools и **неактивные** SQL/Alembic proposals. Наличие файла здесь не означает применения в активной БД.

Не уверен, насколько все старые архитектурные документы соответствуют последней upstream-ветке: репозиторий на сервере отфильтрован, а локальная история неполна.

## 3. Где находится актуальная работа — критично перед запуском

### Git и рабочее дерево

Upstream: https://github.com/bigbrotherdmitriy-prog/pu-workspace . Доступ/полную историю в новой среде проверить отдельно, не подменяя текущие локальные результаты новым checkout.

- Каталог: `/home/codex-worker/workspace/pu-workspace`.
- Ветка: `work/remote-plan-20261030`.
- Локальный HEAD: `d1b3b2a3cd01fcd4548d8d11b2c380d54769c399`.
- `git log` содержит **один** коммит: `Filtered baseline of upstream 2a9faaf; original commit in SOURCE-MANIFEST.json` от 30.10.2026.
- Исходный upstream SHA снимка: `2a9faaf85283d8d20f872333bbdd56f9cbc2685a`, см. `SOURCE-MANIFEST.json`. Отфильтрованы, в частности, `backend/app/act_forms/customer_act.docx` и четыре sales PDF. Не объявлять эти шаблоны отсутствующими в upstream только из-за фильтра.
- В рабочем дереве уже изменены `backend/app/estimates.py` и `backend/tests/test_estimates.py`: сейчас diff 151 insertions / 2 deletions. Не затирать и не приписывать его DeepSeek. Много untracked документов/архивов — это реальные результаты работы, а не мусор.
- `.git` в текущих правах read-only. Полноценный integrated release commit не создан.

Позже для review реконструирован source на подтверждённой тогда upstream-базе `86a4b2b585473fc31fdda44f595965f3c0c82bd2`. Он **не равен локальному HEAD**. Текущий remote main в этой передаче заново не проверялся — **не уверен**, остался ли он тем же.

### Надёжная точка продолжения

**Самая свежая точка после продолжения 03.11:** `docs/operations/C2-ATOMIC-OFFLINE-SOURCE-REVIEW-2026-11-03.tar.gz`, **1291 файл**, SHA256 `aa6370bb396a2cff5d0ed359c99b1c7dab0782456015cb9476b97b5009b5bf2a`; manifest/package-check с тем же префиксом. Включает c1/c2, timezone, offline DTO и новый atomic service `backend/app/mobile_work_log.py` + tests. **39 service/DTO тестов и actual PG конкурентные повторы/rollback/revoked-editor прошли.** Route/API и frontend UI ещё не подключены. Не накладывать повторно старые overlays на этот полный пакет. См. [ATOMIC-OFFLINE-WORKLOG-SERVICE-REVIEW-2026-11-03.md](docs/operations/ATOMIC-OFFLINE-WORKLOG-SERVICE-REVIEW-2026-11-03.md).

**Обновление после прямого разрешения пользователя продолжать работу, 03.11.2026:** последняя точка теперь `docs/operations/C2-MOBILE-CONTRACT-SOURCE-REVIEW-2026-11-03.tar.gz`, **1289 файлов**, SHA256 `3b451beec8b6987656d16d8794b51f6737be6411b49e0b67fc5e42697084c690`. Manifest/package-check с тем же префиксом. Полный кандидат `/tmp/pu-versioned-candidate-20261103`: timezone overlay уже включён, c1/c2 и schema head `c2d3e4f5a6b7` включены только здесь. **118 actual PostgreSQL migrations и filled c1→c2 прошли, Alembic-check zero-diff; 22 NULL-отказа проверены у самой Python-миграции.** Добавлен только offline DTO, 28 pure tests прошли; server handler/receipt/API ещё нет. Всё новое — Codex fallback, не DeepSeek. Корневой checkout по-прежнему не активирован/не закоммичен, production не менялся. Подробно: [VERSIONED-C2-AND-OFFLINE-ENVELOPE-REVIEW-2026-11-03.md](docs/operations/VERSIONED-C2-AND-OFFLINE-ENVELOPE-REVIEW-2026-11-03.md).

Ниже — исторический родитель1284 и отдельные overlays; **не накладывать их второй раз поверх последнего1289-пакета**.

1. Полный ограниченный review-source пакет: `docs/operations/UNIFIED-SCHEMA-REPLAY-SOURCE-REVIEW-2026-11-02.tar.gz`.
2. SHA256: `ff15a66654c2d89a93c50e22f8b7d8d4d5d81437216a3cb9fd06b9a2adc87f04`.
3. Manifest: `UNIFIED-SCHEMA-REPLAY-SOURCE-MANIFEST-2026-11-02.json`; package check рядом. **1284 файла**, backend/frontend/build inputs + synthetic pilot fixture. Это не полный Git-репозиторий: pnpm cache и прочие пути вне declared scope исключены.
4. Проверенный extracted candidate: `/tmp/pu-unified-schema-replay-20261102`. `/tmp` не долговечен; восстанавливать из архива с проверкой каждого SHA.
5. **Последний дополнительный overlay**, ещё не включённый в этот архив: `AI-POLICY-TIMEZONE-REVIEW-2026-11-03.zip`, `CODEX-AI-POLICY-TIMEZONE-2026-11-03.patch`, `AI-POLICY-TIMEZONE-SHA256-2026-11-03.json`. Это два файла: `backend/app/models/ai_policy.py` и новый regression test. Отдельный кандидат `/tmp/pu-ai-policy-timezone-20261103`.
6. `proposals/c1d2e3f4a5b6_material_receipts_and_rd_versions.py` и `proposals/CODEX-tenant-fk-index-2026-11-03.sql` лежат отдельно, **не активированы в backend/migrations/versions**. Не считать их частью production migration chain.

Некоторые новые сервисы (`material_receipts.py`, `drawing_versions.py`, `drawing_compare.py`, `project_creation.py`) могут отсутствовать в корневом checkout и присутствовать только в указанном review-source архиве. Работать с проверенным source, а не переносить отдельные куски наугад.

## 4. Установка, запуск, тесты и выпуск

### Новый изолированный dev-стенд

Источник рецептов: `README.md`, `backend/Dockerfile`, Compose и CI. Команды ниже — инструкции для **собственной тестовой среды**, не приглашение запускать их на production.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
pnpm install --frozen-lockfile
pnpm run check
pnpm run test
# Не очищать tracked react_dist в исходном checkout:
pnpm exec vite build --configLoader runner --outDir /tmp/pu-review-frontend-output
```

CI/Docker фиксируют Node **22.20.0**, pnpm **10.17.1**, Python image **3.12.12**. Локальная проверка выполнена на официальном Node **22.23.4** — это не точное равенство CI runtime. Root `Dockerfile` и root `requirements.txt` в этом snapshot **пусты**: использовать `backend/Dockerfile` и `backend/requirements.txt`.

Для backend нужен отдельный PostgreSQL 16. На новой одноразовой БД, после выбора согласованного source/chain:

```sh
# DATABASE_URL должен указывать ТОЛЬКО на отдельную synthetic/test БД.
cd backend
../.venv/bin/alembic -c alembic.ini upgrade head
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Это стандартный рецепт, не подтверждение готовности текущего active head под все review-функции. Активные 116 ревизий заканчиваются `b9c0d1e2f3a4`; только в частном стенде добавлена c1 как 117-я. Без c1 в активной цепочке нет трёх необходимых schema additions. Нельзя слепо делать `stamp head`/`create_all` или запускать текущий runtime image на существующей БД.

Frontend доступен под `/new/`; backend раздаёт built static. Root Compose публикует `3000:8000`. Он включает не только backend, но и worker/scheduler/Telegram relay: **не запускать весь Compose на этом сервере**. Для локального dev выбирать сервисы и отключать внешние автоматизации до старта. Основной Docker build: `docker build -f backend/Dockerfile --target runtime ...`; development требует явных `PU_BUILD_MODE=development` и `PU_RELEASE_REVISION=local-dev`, production — реального 40-символьного candidate SHA. Образ здесь не собран.

### Переменные окружения — только имена

- База/сессии: `DATABASE_URL`, `POSTGRES_PASSWORD`, `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `BOOTSTRAP_TOKEN`, `AUTH_SESSION_HOURS`.
- Google: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_STORAGE_CLIENT_ID`, `GOOGLE_STORAGE_CLIENT_SECRET`, `GOOGLE_STORAGE_REDIRECT_URI`, `PU_DRIVE_SERVICE_FACTORY`.
- Yandex: `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`, `YANDEX_REDIRECT_URI`.
- AI: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_API_BASE`; `DEEPSEEK_API_KEY` принадлежит GitHub Actions, не локальному приложению/серверу.
- Боты/push: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_USER_ID`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_RELAY_URL`, `TELEGRAM_RELAY_SECRET`, `FCM_SERVICE_ACCOUNT_JSON_PATH`, `PU_ANDROID_CERT_SHA256`.
- Автоматизации/очереди: `GMAIL_AUTO_SYNC_ENABLED`, `AI_SECRETARY_AUTOMATION_ENABLED`, `NOTIFICATION_REFRESH_ENABLED`, `PU_BACKGROUND_EXECUTION`, `ORGANIZER_AUTO_APPLY`, `ORGANIZER_WORKERS`, `PU_JOB_LEASE_SECONDS`, `PU_JOB_POLL_SECONDS`.
- OCR: `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`; остальные OCR/лимитные имена — `.env.example`.
- Выпуск: `PU_RELEASE_REVISION`, `PU_BUILD_MODE`, `PRIMARY_IMAGE`, `PRIMARY_ENV_FILE`, `PRIMARY_SECRETS_DIR`, `PRIMARY_PORT`, `PRIMARY_CONTAINER_PREFIX`, `PRIMARY_VOLUME_NAME`, `PRIMARY_LOCAL_UPLOAD_VOLUME_NAME`.

Для изолированного старта использовать новые synthetic secrets и отключённые внешние автоматизации; не загружать настоящий `.env`. `database.py` имеет fallback URL с hostname `db` — это не безопасная автоматическая настройка тестов. Сначала явно задать test `DATABASE_URL`.

### Пример ограниченной backend-проверки

Из корня **распакованного review candidate** с подготовленным `.venv`, не из production checkout:

```sh
env -i PATH="$PATH" DATABASE_URL=sqlite+pysqlite:///:memory: \
  PYTHONPATH=backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider \
  backend/tests/test_material_receipts.py \
  backend/tests/test_response_draft_index_parity.py
```

Это synthetic service subset, не HTTP/PG/full backend acceptance. При воспроизведении строго запрещённых внешних операций использовать audit-guard рецепт из evidence соответствующего прогона. Полный frontend после `pnpm install` — `pnpm run test`; на этом2GB хосте единый180s deadline оказался недостаточен: использовать сохранённые10групп и `--maxWorkers=1`.

### Как проверяли здесь

- `/tmp/pu-stage1-venv/bin/python3`: временный Python venv, часть дополнительных зависимостей ставилась отдельно. Для переноса перепроверить requirements.
- Node: `/tmp/pu-node-runtime-20261102/node-v22.23.4-linux-x64/bin/node`; pnpm: `/tmp/pu-node-tools-20261102/node_modules/pnpm/bin/pnpm.cjs`; store `/tmp/pu-pnpm-store-20261102`.
- Backend service tests: `env -i`, memory/temp SQLite, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONPATH=<candidate>/backend`, pytest `-p no:cacheprovider`. Audit hooks запрещали network/subprocess/system; concurrency cases работали на временной SQLite.
- **Не запускать общий backend pytest без проверки fixtures**. Документированный PG test opt-in — `PU_TEST_POSTGRES=1` и отдельная DB с test-именем. Перед запуском прочитать конкретные tests/conftest; полный backend suite здесь не принят.
- HTTP/TestClient ранее зависал в sandbox; запросы вне sandbox были остановлены/не разрешены. Это отдельный блокер. Не обходить его direct route calls и не называть service tests проверкой HTTP/CSRF.
- Временный PostgreSQL: `/tmp/pu-pg-runtime-20261102`, native 16.2 из SHA-проверенного wheel pgserver 0.1.4; собственный Python-wrapper пакета не использовался. Только Unix socket, маленькие memory limits, без системной службы.
- Каждый private PG runner имел отдельное разрешение, fixed targets, `finally` stop. После последних запусков сервер остановлен. Scripts в `docs/operations/tools/check_portable_postgres_*.py` **одноразовые**: повторный CREATE DATABASE или существующий dump обязан остановить запуск. Не запускать их все подряд и не менять пути для скрытого повторения отказанного действия.

### Production и deployment

Полный pipeline source → Git commit → immutable image → delivery → release gate **не завершён**. `backend/Dockerfile` runtime CMD автоматически запускает Alembic; это не no-DB-change deployment.

Существует ограниченный `/usr/local/bin/pu-release` (`status`, `preflight SHA`, `apply SHA`). Gate проверяет source hash, immutable image ID/revision, expected current SHA, срок и root-owned одноразовое разрешение; миграции требуют отдельного решения. **Не создавать approval, не запускать apply без разрешения конкретного релиза.** Последний сохранённый production status от 31.10: SHA `2a9faaf85283d8d20f872333bbdd56f9cbc2685a`, status ok; сейчас заново не проверялся — не уверен в текущем состоянии хоста.

См. `docs/operations/RELEASE-CANDIDATE-DELIVERY-2026-10-31.md`, `docs/operations.md`, `infra/primary/docker-compose.yml`. Исторический production proxy override и first-host primary stack — разные схемы; не смешивать Compose-файлы. Документированные Telegram/Gemini health checks не разрешают трогать действующих ботов в рамках этой задачи.

## 5. Фактическая готовность

| Проверено | Доказательство и границы |
|---|---|
| Подготовка и связь журнала/сметы | Stage0 и bounded synthetic journal invariants отмечены done в control. Не весь этап1. |
| Объединённый backend subset | **102 passed / 4 modules / 50.35s** на 1284-file candidate: receipts, ResponseDraft indexes, composite FKs, materialization lifecycle. Не весь backend/API. `UNIFIED-SCHEMA-REPLAY-PACKAGE-CHECK-2026-11-02.json`. |
| Frontend | **659 passed / 102 files**, 0 failed/pending: 643 в 10 группах + ранее16 offline, один и тот же source. Не единый Vitest process и не full CI. `FRONTEND-BATCH-RESULTS-2026-11-02.json`, raw report ZIP. |
| TypeScript/build | `tsc --noEmit` exit0; Vite build exit0, 90 output files. Отдельный output `/tmp/pu-frontend-build-20261102`, hashes в `FRONTEND-BUILD-RESULT-2026-11-02.json`. Не E2E/реальный браузер. |
| Исторические broader service checks | В более раннем 1281-file candidate были308passed/19modules,7HTTPexcluded. Не суммировать с102 и не переносить автоматически на новый overlay. |
| PG миграции | Реально выполнены116 ревизий, заполнение, затем c1 как117-я: 8 старых строк/7таблиц и512байт фото сохранились; actual PG receipt replay count1. `ACTUAL-PG-POPULATED-UPGRADE-RESULT-2026-11-02.json`. |
| PG backup/restore | Отдельная synthetic DB:133tables/16rows совпали по row hashes, columns/FK/index reflection; receipt replay в READ ONLY успешен. `PG-RESTORE-VERIFIED-2026-11-03.json`. Не ACL/sequence/CAS/live consistency acceptance. |
| Последний timezone overlay | Regression RED1fail → GREEN1pass; actual PG type diff исчез, в исправленном SQL-клоне осталось22nullable. `AI-POLICY-TIMEZONE-PG-DRIFT-2026-11-03.json`. Большие suites на этом overlay заново не запускались. |

Частично реализованы: numeric/import/КС, customer portal, замечания/версии, отчёты, складские primitives, tenant guard. Наличие кода, draft PR или успешно созданного файла не равно приёмке этапа.

Не приняты: полный backend HTTP/required CI, реальная смета/формы заказчика, телефон/камера/PWA, реальный обмен Диадок/1С, PDF-сравнение, складские списания/корректировки, полный self-service и production release.

## 6. Оставшиеся задачи по приоритету

| Приоритет | Задача | Основные файлы/источники |
|---|---|---|
| P0 | Сохранить uncommitted/untracked работу, собрать отдельную Git-ветку из exact source capsule + timezone overlay с проверкой hashes и origin. Не выдавать upstream SHA за интегрированный commit. | Корневые изменённые estimates/test_estimates, `docs/operations/UNIFIED-SCHEMA-REPLAY-*`, `AI-POLICY-TIMEZONE-*`, `SOURCE-MANIFEST.json`. |
| P0 | Закрыть22nullable drift: fail-closed NULL preflight, решение обоснованного ORM/migration contract, meaningful NULL/non-NULL synthetic fixtures, rollback; никаких выдуманных дат/backfill now. | `TIMESTAMP-NULL-TYPE-PREFLIGHT-2026-11-03.json`, `TIMESTAMP-MIGRATION-PROVENANCE-2026-11-03.json`, `models/management.py`, `models/execution_finance.py`, `models/ai_policy.py`, соответствующие migrations. Полный список полей в JSON. |
| P0 | Интегрировать c1 + два tenant FK/index только после review и корректного inactive→active migration решения; проверить полный drift, schema.py/head и filled upgrade/restore. | `backend/migrations/versions/`, `backend/app/schema.py`, `docs/operations/proposals/`, `check_portable_postgres_complete_drift.py`. |
| P1 — этап1 | Округления, числовые края, конкурентные КС/журнал, proper API; сверка реальной сметы и Word/Excel форм. | `estimates.py`, `estimate_import.py`, `work_log.py`, `ks_forms.py`, `api/estimates.py`, `api/acceptance_acts.py`, `test_estimates.py`, `test_work_log.py`, `docs/STAGE1-ROUNDING-DECISION-2026-11-01.md`. |
| P1 — этап2 | Proper HTTP права двух пользователей, чужой проект, отзыв, повторное/устаревшее решение и история; concurrency. | `customer_portal.py`, `api/customer.py`, `core/auth.py`, `test_customer_portal.py`. |
| P1 — этап3 | Offline create журнала/замечаний: typed envelope, editor при первичной доставке и replay, атомарные receipt+fact, entity ID контракт, immutable date/payload. Auth-required recovery; затем actual IndexedDB/multitab/телефон. | `api/mobile_sync.py`, `models/mobile_sync.py`, `api/work_log.py`, `work_log.py`, `frontend/src/offline/`, `modules/work-log/WorkLogModule.tsx`, `modules/defects/DefectsModule.tsx`; `docs/deepseek/OFFLINE-WORKLOG-SERVER-ACCEPTANCE-2026-11-02.md`. |
| P2 — этап4 | Контракт конкретной1С/Диадока, scoped API keys/revocation/audit, retries без дублей. Пока только synthetic/mock. | `backend/app/integrations/`, `api/integrations.py`, существующие integration tests; утверждённый MASTER-PLAN. |
| P2 — этап5 | Выбрать3формы по реальным потребностям, подтвердить dependencies/данные/печать; visual PDF и реальный образец заказчика. | `api/id_forms.py`, `id_forms.py`, `api/id_documents.py`, отчётные helpers в capsule, `docs/STAGE5-*`. Не обещать100форм. |
| P2 — этап6 | Физические остатки/явное списание/корректировки с историей; не конвертировать объём работы в расход. PDF version comparison; tenant-safe onboarding. | Candidate `material_receipts.py`, `purchase.py`, `models/purchase.py`, `drawing_versions.py`, `drawing_compare.py`, `defects.py`, `project_creation.py`, `api/projects.py`, `onboarding.py`, `docs/STAGE6-*`. |
| Финальный gate | Required CI на exact integrated head, image/revision/source hash, inert delivery, rollback, отдельное release approval. | `backend/Dockerfile`, `.github/workflows/`, `scripts/release/`, `scripts/rollback-primary-release.sh`, release docs и pu-release gate. |

## 7. Договорённости и отвергнутые подходы

- Пользователь уже разрешил обычную независимую работу: не спрашивать рутинные подтверждения. Но это **не** обход sandbox/отказанных операций, не разрешение конкретного production apply и не основание выдумать результат.
- По AGENTS DeepSeek пишет code/tests, Codex курирует. Прямое позднее исключение: если DeepSeek не может выполнить задачу, Codex может реализовать сам, явно маркируя fallback origin. Новая передача назначает Claude продолжателем; не уверен, есть ли у Claude технически настроенный dispatch. Проверить capabilities, не читать ключи.
- Source SHA, exact diff/head, tests и provenance обязательны. Генерация/публикация PR не равна tests/review/merge.
- Все изменения до приёмки изолированы. Никогда не тестировать на production DB или живом проекте Дубна–Городец.
- Нельзя менять sales-bot, spam-jester, proxy, AppArmor и системные службы. Не устанавливать новый runner/dispatcher; setup принадлежит другому контуру.
- Чужой проект должен отвергаться на сервере, не только скрываться в UI. Legacy tenant NULL policy не угадывать.
- Не исправлять readonly authorization/HTTP blocker запуском direct route function: это не эквивалент HTTP/CSRF проверки.
- Не удалять исторические приходы/версии, не придумывать destructive downgrade/GC. SQL proposals fail closed, без автоматического исправления данных.
- Не ослаблять FK/composite scope ради зелёного Alembic. Два избыточных простых ORM FK были убраны только с сохранением исторических составных RESTRICT, NOT NULL и synthetic checks.
- Не подавлять nullable/type drift или warning циклов. Старый неполный сбор исправлен, см. ниже.
- Общий Vitest180s timeout не был принят как PASS/причина переписать тесты: все существующие файлы проверены10группами с пофайловыми отчётами. Это не доказывает отсутствие межфайловой интерференции в одном процессе.

## 8. Известные дефекты, долги и риски

1. **Критическая поправка к старым статусам drift.** Collector раньше пропускал вложенные lists в Alembic error.diffs, теряя modify_nullable/modify_type. Прежнее «3remaining» было неверным полным подсчётом. До timezone fix полная картина: исходный privateDB26ops, tenant-SQL clone23ops. После timezone overlay: исходныйDB25ops, clone22ops. Raw `COMPLETE-PG-DRIFT-2026-11-03.json` — старый полный снимок; `AI-POLICY-TIMEZONE-PG-DRIFT-2026-11-03.json` — более свежий. Оба check остаются FAIL, даже когда diagnostic runner exit0.
2. 22timestamp-колонки БД nullable=True при model nullable=False; проверенные synthetic tables пусты. Не делать массовый NOT NULL без безопасного data preflight, и не считать отсутствие synthetic NULL гарантией production.
3. SQLite full migration chain ломается на legacy d93b5f607c82 ALTER COLUMN SET NOT NULL. На частично выполненной DB не делать blind retry. SQLite нельзя подменять полноценной PG-приёмкой.
4. SQLAlchemy предупреждает о циклах `documents/organizations/projects`; FK completeness в autogen не доказана одним зелёным числом.
5. В restore сначала был strict FAIL: один partial-index predicate после pg_dump/restore меняет запись array casts. Все133row hashes совпали, конкретные два выражения проверены30PGcases включая NULL. Не вводить общий ignore predicate и не стирать первоначальный FAIL. Sequences/ACL/functions/triggers/CHECK отдельно не приняты.
6. Auth-required offline команды пропускаются даже при manual sync; восстановление после повторного входа не найдено. Не слать автоматически все403: это может быть отзыв прав.
7. Offline work-log/defect create отсутствует; текущий command DTO содержит task.update/notification.mark_read. Добавления Literal недостаточно: текущий role/else dispatch ошибочно обработает новую операцию как notification, entity_id пока positive required.
8. Derived price rounding не согласовано: qty2/total2.01 без явной цены. Не считать эквивалентными исходный итог2.01 и rounded price1.01 → итог2.02; см. decision doc.
9. Warehouse `prepare_writeoff` намеренно возвращает `applied=False`; полноценного движения/резерва нет. Политики отрицательного остатка и удаления связанного журнала ещё не согласованы.
10. Historical receipt replay после изменения current unit был сломан, **исправлен только в candidate**: сравнение existing immutable receipt до проверки новой current unit, ownership сохраняется. Не потерять overlay при переносе.
11. `purchase.rebuild` сам делает commit; не включать его как будто это flush-only часть будущей атомарной складской транзакции.
12. CAS использует Linux filesystem capabilities (flock, anchored/no-follow access, renameat2 no-replace, xattrs). Временная FS проверена, production FS capability/permissions не приняты. Rollback может оставлять quota-counted blob; power-loss/GC/distributed atomicity не обещаны.
13. Временный venv отдельно получил ReportLab; в `backend/requirements.txt` **ReportLab не перечислен**. Перед переносом PDF prototype подтвердить его import path и воспроизводимые runtime dependencies. Не считать ручную pip-установку частью image.
14. Root Dockerfile/requirements пусты; source capsule scope не полный repo; tracked legacy react_dist не гарантия актуального frontend. CI Docker build здесь не выполнен.
15. В этой среде ~2GB RAM, нет разрешённого Docker daemon/sudo. Не менять права socket/службы ради сборки. `/tmp` может исчезнуть.

## 9. Интеграции, ключи и история последних работ

### Внешние подключения

| Сервис | Назначение/код | Где искать конфигурационный контракт, не секрет |
|---|---|---|
| PostgreSQL | Основная DB, durable state | DATABASE_URL, Compose, database.py. Production URL не читать/не использовать для tests. |
| Google Drive/Gmail/Tasks/Calendar | Документы, почта, внешние задачи/события | Google API modules; client env names, project-bound OAuth records. TOKEN_ENCRYPTION_KEY относится к защите токенов; сами записи/ключ не извлекать. |
| Yandex Disk/Mail | Альтернативные хранилище/почта | integrations и api/yandex_*.py, YANDEX_* names. Реальные настройки здесь не проверялись. |
| Gemini | Анализ/AI Secretary | GEMINI_* names и policy modules. Не отправлять реальные документы без полномочий. |
| DeepSeek | Генерация bounded PR через GitHub Actions | По переданным данным API key только в GitHub Actions Secret `DEEPSEEK_API_KEY`, не на сервере. Не копировать его. |
| Telegram/FCM | Уведомления/Android | TELEGRAM_*, FCM_SERVICE_ACCOUNT_JSON_PATH. Production bots неприкосновенны. |
| Диадок/1С | Планируемый контролируемый обмен | Реальные API/конфигурация1С не предоставлены; работоспособность не подтверждена. |

Private primary env задаётся `PRIMARY_ENV_FILE`; file-secrets через `PRIMARY_SECRETS_DIR` монтируются read-only в `/run/pu-secrets` согласно Compose. **Фактические значения/наличие на production не проверялись; не уверен в их текущем размещении.** Не просить ключи в чат. Limited release использует отдельный серверный SSH key `~/.ssh/pu-production-release` и forced-command gate; это только сообщённое имя файла, содержимое не читать/не копировать. Root key не нужен. Адрес production передан ранее как `72.56.108.162`, текущая доступность не проверялась.

### Git/PR история и текущие квитанции

Полной истории upstream локально нет. Единственный `git log` commit описан в разделе3. Не приписывать локальным patches несуществующие коммиты. Ниже — сохранённая история review/передачи, не новый remote git log:

- PR363 publication fix: в прежней передаче сообщён merge `d36e99b22a074ede052141ea72eedbd8828300c1`. Заново remote merge здесь не проверялся.
- DeepSeek PR366/367 — исходные preview/numeric задачи; последующие review привели к дополнительным дефектам и corrections, поэтому старые80green не являются приёмкой нового head.
- PR379 head `e86e65e5c75ebb5cb1e83061a17494dd17814a06`: immutable offline task retries. Его exact2files включены в1284capsule; Gitblob hashes и6runtime tests подтверждены.
- PR392 RD model, PR409/410/413 defect service/tests/API, PR421 receipt model, PR423 report tests — происхождение отдельных деталей в evidence. **Не считать автоматически слитыми или полностью принятыми**.
- Последние локальные изменения: receipt historical replay fix; ORM unique-index/composite FK parity; объединение1284filecapsule;659frontend tests/tsc/build;filledPGupgrade иscopedrestore; tenant SQL clone tests; correction полногоdriftcollector; последний AI policy timezone2fileoverlay.

Последний проверенный status DeepSeek:

| Задание | Request / run | Фактический результат |
|---|---|---|
| DS24 schema proposal | `af2dccf3b330f823880c40468c0b1447` | rejected: `Invalid bounded request`, не queued/не PR. Не повторять вслепую. |
| DS07B retry state tests | `aa94443dc25f874a6a1785d2d2678242`, run41504537036 | failed / cancelled, PR нет. |
| DS07C work-log envelope | `6216727a3ba96004685cf652f9b1daa3`, run41504200702 | failed / failure, PR нет. |

Дополнительная read-only диагностика jobs: DS07C упал на `Generate bounded source changes` после успешных checkout; создание PR skipped. У DS07B jobs API вернул0. Первопричина ошибки генерации не установлена — **не уверен**; вызов модели не объявлять успешным. См. [MOBILE-DEEPSEEK-JOB-DIAGNOSIS-2026-11-03.json](docs/operations/MOBILE-DEEPSEEK-JOB-DIAGNOSIS-2026-11-03.json).

`/usr/local/bin/pu-deepseek` принимает только `task`, `context_files`, `source_ref`; context paths должны существовать на указанном remote SHA. **Передачи локального source файла этим клиентом нет**. Дополнительно проверен exact публичный генератор: он разрешает output **только в уже существующие context paths**. Новые файлы DS07C/DS24 в context не входят, поэтому их постановки несовместимы с этим контрактом. Это подтверждённый дефект постановок, но не доказанная первопричина конкретного failed run. См. [DEEPSEEK-OUTPUT-CONTRACT-AUDIT-2026-11-03.json](docs/operations/DEEPSEEK-OUTPUT-CONTRACT-AUDIT-2026-11-03.json). Ранее некоторые helper существуют лишь в capsule — такой контекст нельзя выдумывать как remote. Не добавлять повторные requests с другим id, скрывая тот же отказ.

Сам факт существования timer/очереди не гарантирует непрерывность. На последней проверке `.pu-autonomy/control.json` имел `mode: running`. Подготовка HANDOFF его не меняла: пользователь не поручал pause. Перед работой Claude проверить, не выполняется ли старый куратор; владение очередью должно быть единственным. Не запускать второй ручной процесс и не менять mode самостоятельно как будто пользователь уже поручил остановку. Службы не менять. Доступность Claude к старому server-side dispatch — не уверен.

## 10. Первые шаги для продолжателя

При переносе на другой хост одной Git-ветки недостаточно: отдельно сохранить этот `HANDOFF.md`, `AGENTS.md`, MASTER-PLAN, `docs/operations` с нужными архивами/manifest/overlays/results, постановки `docs/deepseek`, актуальный `control.json` и относящиеся к нему evidence/receipts. Не копировать настоящий `.env`, `.aws`, SSH/OAuth/service-account файлы, production dump или посторонние личные документы. Сам source capsule не включает весь workflow/историю Git; `/tmp` и старую серверную инфраструктуру нельзя считать автоматически переданными.

1. Прочитать этот файл, AGENTS и MASTER-PLAN; проверить текущие `git status`, mode/очередь и receipt **до новых dispatch**. Не потерять untracked artifacts при clean/reset/clone.
2. Проверить SHA последнего1291filecapsule выше, распаковать в отдельный каталог, проверить manifest. Timezone overlay и c1/c2 уже включены; повторно не накладывать. Отдельно сохранить корневой estimates diff. Создать reviewable integration branch в среде, где Git write разрешён; никаких автоматических merge/deploy.
3. Начать с22nullable timestamp differences. Полный список/исторические Column declarations уже сохранены. Подготовить инертное fail-closed решение; проверить на meaningful NULL/non-NULL fixture и rollback. Не угадывать production данные и не заполнять исторические даты текущим временем.
4. После этого связать schema.py, active revision graph и проверенные c1/tenant proposals. Проверить корректный exact source на filledPG/restore и полном nested-aware drift, не только число верхнеуровневых операций.
5. Диагностировать failed/cancelled DeepSeek runs и rejected DS24 по реальному контракту, без обхода approval/пустых повторов. Существующие frontend659 и backend102 не повторять ради статуса; повтор нужен после нового интеграционного изменения/риска.
6. Продолжать этапы MASTER-PLAN по приоритету. Не маскировать отсутствие HTTP/реальных форм/phone проверок synthetic тестами. Не отдавать production gate source без реального integrated SHA/image.

### Нерешённые вопросы пользователю / владельцу продукта

Ответы не требуются, чтобы продолжать независимые проверки, но их нельзя выдумать:

1. Где реальная смета и принимаемые заказчиком КС-2/КС-3/образцы форм ИД?
2. При qty2/исходном total2.01 без unit price сохранять total2.01 или округлять price1.01 и пересчитывать total2.02?
3. Списание сверх физического остатка запрещать или допускать отрицательный остаток?
4. Удаление журнала с расходом блокировать до сторно или сохранять расход после удаления записи? Какой порядок корректировок истории?
5. Какая конкретная конфигурация/формат1С, тестовый сценарий Диадока и три приоритетные формы ИД?
6. Какой способ проверки реального телефона/камеры/PWA доступен? Секреты и реальные заказчики для synthetic acceptance не нужны.

Полный план **не завершён**. 22nullable закрыты в последнем review candidate c2; следующие шаги —его Git/CI integration и API/UI-подключение уже проверенного atomic offline-create service; следующий production шаг без отдельного разрешения конкретного релиза отсутствует.
