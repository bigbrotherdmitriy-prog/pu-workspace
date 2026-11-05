# HANDOFF — PU Workspace

## Текущая передача — 05.11.2026

Последний коммит проектного кода: **b894d194826a2bf25655b8e3d4b7067bc3413560**, ветка `work/post-handoff-safe-20261104`, `/tmp/pu-integration-git-20261103`. После него — отдельный завершающий коммит документации. Точный HEAD переносимого снимка указан в `docs/operations/OFFLINE-LOGOUT-CURRENT-TRANSFER-INPUTS-2026-11-05.json`. Корневые исходные estimates diff и untracked сохранены. Реализация — **Codex fallback**, не DeepSeek. Ничего не pushed/merged/deployed.

**P0 offline logout закрыт в проверенном объёме.** Офлайн сразу закрываются локальная сессия и приватный интерфейс, удаляются доступные auth-cookie и owned CacheStorage. Очередь отзыва конкретной серверной сессии переживает перезагрузку; после появления сети отзыв подтверждается сервером. Старые cookie/токены, поздний ответ JSON, кеш и BFCache не снимают блокировку. Только явный подтверждённый Login открывает новую сессию. Сохранённые actor-scoped фото/правки/операции не удаляются; до выхода всегда есть предупреждение и возможность отмены. Несохранённые поля формы нужно сохранить до подтверждения.

После исправлений проверено: **877 unit/jsdom frontend /121files**, **100 Playwright E2E /30files**, **4227 Python /433modules**, **132 conditional SKIP**; дополнительно5subchecks. Это матрицы полных прогонов и целевых повторов, не один чистый monolithic запуск на финальном HEAD. Исходные красные отчёты сохранены. Семь тестовых файлов sales-bot/DeepSeek dispatcher исключены по прямому запрету трогать эти подсистемы. TypeScript/main build/E2E typecheck/E2E build —0. Отдельно4 реальных Chromium→HTTP→FastAPI/memorySQLite logout checks PASS; собственные процессы остановлены.

Основные доказательства: `OFFLINE-LOGOUT-VERIFICATION-2026-11-05.json`, `OFFLINE-LOGOUT-PYTHON-FINAL-MATRIX-2026-11-05.json`, `OFFLINE-LOGOUT-E2E-FINAL-MATRIX-2026-11-05.json`, `OFFLINE-LOGOUT-FRONTEND-FINAL-877-2026-11-04.json` в docs/operations. Даты в старых именах исторические; timestamps внутри фактические. 132пропуска не являются приёмкой PostgreSQL/внешних контуров. Реальный телефон/камера/OS install/формы заказчика отдельно, полного MASTER-PASS нет.

HttpOnly-cookie и весь дисковый HTTP-кэш нельзя физически удалить JavaScript без сети. Реализованы fail-closed local/server fence, очистка управляемого кэша и последующая недействительность серверной сессии. Полные допущения — NOTES.md; destructive purge прежней версии отменён текущим заданием.

Следующий P0: **две подтверждённые ошибки сметного округления** (`STAGE1-ROUNDING-TIE-RED-2026-11-04.json`, `STAGE1-MANUAL-ROUNDTRIP-RED-2026-11-04.json`). DS39–44 queued не доказывает исполнение.

## 1. О проекте

PU Workspace — веб-приложение строительного подрядчика: договоры и документы, сметы, журнал фактических работ, КС-2/КС-3, кабинет заказчика, замечания с фото, закупки, чертежи, задачи и управленческие финансы. Есть AI-помощники и подключения почты/хранилищ. README раннего organizer MVP отражает не весь продукт.

Основной документ — `MASTER-PLAN-2026-10-30.md`; checklist 30.10 — приёмка, roadmap — справочный. Исходники последних двух сохранены в `docs/operations/inputs/`. ТЗ 13.10 план не заменяет. 60 рабочих дней — оценка, не обещанный срок.

КС: объёмы только из журнала, цены из сметы, остаток не превышается. Оплата не означает физический приход; объём работы не превращается автоматически в расход материала.

## 2. Стек и архитектура

| Слой | Код и связи |
|---|---|
| Backend | Python 3.12, FastAPI 0.141.1, Starlette 1.3.1, Uvicorn 0.35.0. `backend/app/main.py` собирает API/lifespan/static; `api/` содержит DTO и guards. |
| БД | PostgreSQL 16, SQLAlchemy 2.0.43, psycopg 3.2.9, Alembic 1.16.5. `database.py`, `models/`, `migrations/versions/`, `schema.py`. |
| Frontend | React 19, TypeScript 5.9.3, Vite 7.3.7; `frontend/src/App.tsx`, `modules/`, `offline/`, pnpm lockfile. |
| Проверки | pytest 9.1.1, Vitest 4.1.12/jsdom; Playwright 1.58.2 существует, реальный E2E здесь не принят. |
| Документы | openpyxl/xlrd/pypdf/Pillow/ReportLab 4.4.3; OCR Tesseract/Poppler/antiword, JRE/MPXJ/JPype для графиков. |

- `estimate_import.py` → `estimates.py` / `work_log.py` → `ks_forms.py`: импорт, факт, распределение по актам, Word/Excel. `acceptance_acts.py` — отдельный модуль актов.
- `customer_portal.py`, `api/customer.py`, `modules/customer/`: доступ, решения и история AuditLog. Новый `KsAct.decision_revision` защищает решение от старой отправки акта.
- `core/auth.py`, `api/auth.py`, `models/project_member.py`, `workspace_tenant.py`: сессии, CSRF, роли и tenant.
- `api/mobile_sync.py`, `mobile_work_log_contract.py`, `mobile_work_log.py`, `models/mobile_sync.py`, `frontend/src/offline/`: typed create, атомарный fact+receipt, очередь и повторы. `/mobile-sync/work-log-create` не требует фиктивного entity_id; legacy `/commands` сохраняет task/notification контракт.
- `defects.py`, `api/defects.py`, `drawing_versions.py`, `drawing_compare.py`, `models/rd.py`: замечания, фото, версии и сравнение.
- `material_receipts.py`, `purchase.py`, `models/purchase.py`: физический приход и закупки. `project_creation.py`, `api/projects.py`, `onboarding.py`: tenant guard/self-service.
- `work_log_form.py`, `id_handover.py` / `equipment_test_act.py`, `api/id_forms.py`, `api/id_documents.py`: отчёты и формы. `integrations/`, Google/Yandex API, AI policy/secretary/gemini — внешние провайдеры.
- `jobs/worker.py`/scheduler и `PU_BACKGROUND_EXECUTION` — задания приложения, отдельно от очереди разработки DeepSeek.
- `backend/app/react_dist/` — сборка, не исходники; review build направлять в отдельный `--outDir`.

## 3. Git и перенос

Корневой worktree: work/remote-plan-20261030, d1b3b2a3cd01fcd4548d8d11b2c380d54769c399, .git read-only. Не clean/reset и не удалять untracked. Два исходных estimates diff не накладывать автоматически на новую ветку. Изменения и коммиты находятся в отдельном checkout выше, upstream86a4b2b585473fc31fdda44f595965f3c0c82bd2. Текущий remote main — **не уверен**, как releasebase не сверялся.

Актуальный перенос определяется только `docs/operations/OFFLINE-LOGOUT-CURRENT-TRANSFER-INPUTS-2026-11-05.json`. Рядом — новый SOURCE tar.gz, MANIFEST, FULLbundle и `OFFLINE-LOGOUT-CURRENT-RESTORE-2026-11-05.json`. Inputs связывает точный HEAD, SHA256 и обязательные старые metadata/cumulative prerequisites. Старые2f3bc40d и ZIPee83e373 offline logout не содержат.

```sh
python3 docs/operations/tools/restore_current_review.py \
 --package-root "$PWD" \
 --inputs docs/operations/OFFLINE-LOGOUT-CURRENT-TRANSFER-INPUTS-2026-11-05.json \
 --destination /tmp/pu-review-new --report /tmp/pu-review-new-report.json
```

Destination/report должны быть новыми. Helper проверяет archive/member SHA256, Git blobs, точный HEAD, чистый publicpartial worktree и отсутствие remotes. Частные пропущенные blobs не checkout. Допущен только точный публичный .env.example с placeholders; реальные .env/credentials/owner tokens не входят. Если текущего Inputs/RESTORE нет, упаковка ещё не закончена: завершить её из текущего checkout, не выдавать старый пакет за новый.

Это public review source, не production image и не готовая доставка source+image. Dependencies/runtime не входят. Curator stand scripts содержат пути данного сервера и временных runtimes: перед использованием в другом checkout сверить constants и source head; не принять проверку старого /tmp дерева за проверку нового кода. Актуальные evidence/WORK-STATUS и tools передавать рядом как companion.

## 4. Запуск, проверки, деплой

Рецепт для новой изолированной dev-среды; свежая установка здесь не выполнялась:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt PyYAML
cd frontend
pnpm install --frozen-lockfile
pnpm run check
pnpm exec vite build --configLoader runner --outDir /tmp/pu-review-frontend-output
cd ..
```

Локальные проверки использовали `/tmp/pu-stage1-venv` и официальный Node22.23.4 с существующими зависимостями. CI/Docker закрепляют Node22.20.0/pnpm10.17.1/Python image3.12.12; идентичность сред не доказана.

Backend: отдельная временная PostgreSQL16 и явный `DATABASE_URL`; приложение запускается из `backend/` командой `uvicorn app.main:app --host 127.0.0.1 --port 8000`, frontend раздаётся под `/new/`. `database.py` имеет fallback hostname db — для тестов URL всегда задавать явно. Никогда не направлять запуск/pytest/migrations на неизвестную существующую БД.

**Текущая схема c5 `c5d6e7f8a9b0`,121ревизия.** Actual private PostgreSQL16.2 fresh chain, populatedc4→c5, Alembic zero-diff и quiescent backup/restore136tables/104sequences выполнены. Проверены SQLconcurrency decisionCAS/arrivalidempotence/stockguard, отдельноphotoquota RED13→GREEN12. Это собственный Unixsocket/tmpstand, не production. Остановкаfinally подтверждена reports. Downgrade destructivecolumns/history намереннонеавтоматический — нужен reviewedrestore. Model/schema/API/client выпускаютсясовместно.

543backendchecks/3deselected на baseline f7e79a2c включаютrealTestClient и memorySQLite; bearer/role/replay/revoke проверены наsyntheticusers. Не приложениеapp.main/startup целиком и не полнаяGitHubCI/image. HTTP/PG больше не являются прежним техническимблокером: использовать штатноразрешённый standrunner, не повторять старыеотклонённые команды/службы.

Тесты запускать выборочно, с очищенным окружением, synthetic DB/storage и запретом внешних операций; точные выполненные команды/отчёты в evidence. В sandbox Unixsocket/TestClient не работают; новый прямой допуск пользователя реализован штатным authorized запуском. Обхода denied/pending операций и вызовов чужих служб нет. Не запускать весь pytest вслепую: есть PG/HTTP/E2E зависимости.

Root Dockerfile/requirements.txt пусты; использовать backend-версии. Compose содержит DB/worker/scheduler/Telegram relay — целиком здесь не запускать. Build contract: `PU_BUILD_MODE`, `PU_RELEASE_REVISION`; image/source delivery и required CI не приняты. Production deploy/approval запрещены; существующий pu-release guard не обходить.

Переменные окружения — только имена, без значений:

- БД/сессии: `DATABASE_URL`, `POSTGRES_PASSWORD`, `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `BOOTSTRAP_TOKEN`, `AUTH_SESSION_HOURS`.
- Google: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_STORAGE_CLIENT_ID`, `GOOGLE_STORAGE_CLIENT_SECRET`, `GOOGLE_STORAGE_REDIRECT_URI`, `PU_DRIVE_SERVICE_FACTORY`.
- Yandex: `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`, `YANDEX_REDIRECT_URI`.
- AI: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_API_BASE`; `DEEPSEEK_API_KEY` — GitHub Actions Secret, не локальный app secret.
- Push/боты: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_USER_ID`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_RELAY_URL`, `TELEGRAM_RELAY_SECRET`, `FCM_SERVICE_ACCOUNT_JSON_PATH`, `PU_ANDROID_CERT_SHA256`.
- Автоматизации: `GMAIL_AUTO_SYNC_ENABLED`, `AI_SECRETARY_AUTOMATION_ENABLED`, `NOTIFICATION_REFRESH_ENABLED`, `PU_BACKGROUND_EXECUTION`, `ORGANIZER_AUTO_APPLY`, `ORGANIZER_WORKERS`, `PU_JOB_LEASE_SECONDS`, `PU_JOB_POLL_SECONDS`.
- OCR: `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`.
- Release: `PU_RELEASE_REVISION`, `PU_BUILD_MODE`, `PRIMARY_IMAGE`, `PRIMARY_ENV_FILE`, `PRIMARY_SECRETS_DIR`, `PRIMARY_PORT`, `PRIMARY_CONTAINER_PREFIX`, `PRIMARY_VOLUME_NAME`, `PRIMARY_LOCAL_UPLOAD_VOLUME_NAME`.


Не читать настоящий .env или секреты; dev-автоматизации и провайдеры отключать.

## 5. Состояние и границы

| Участок | Проверено | Остаток |
|---|---|---|
| Смета/журнал/КС | finite/bounds/import/fact-reservations/periods/остатки и HTTP; strictderivedprice настройка, legacyline читается/небиллитсямолча | Две ошибки округления DS39–42; реальнаясмета и принимаемыеформы/визуальнаяпечать |
| Заказчик/права | TestClient two-user/foreign/revoke, настоящие bearerсессии/отзыв, PGdecisionCAS | Realcustomerworkflow/браузернаяприёмка согласования |
| Объект/offline | TypedmetadataUI + realIDB queue/replay/strictreceipts/actor/session/quotas; missingackretry вbrowser; photoquotaSQLrace исправлена | Реальныйтелефон/камера/PWAupdate; bounded offlinephoto Blob+typed receipts/retry после reload проверены; realcamera/phone отдельно |
| Склад | ManagerAPI receipts/writeoffs/storno, явныеобъёмы, nonegative default, PGconcurrency/idempotency, journaldeletehistoryguard | Отдельная physical UI/history и durable pendingintent проверены; реальный warehouse workflow/политики предварительные |
| Чертежи | Version/content/retention/rasterhelpers; immutable normalizedpointAPI, scoped version picker/point UI иtenant tests | Точка—metadatareference, не proof существованияPDFстраницы/отрисовки; realdrawing UX/manual |
| Формы | 7явноsyntheticDOCX/XLSX outputs,3IDпримераexistinggenerators, XML/CRC/printarea/A4checks | Это не3новыеутверждённыеcustomerформы и неWord/Excelvisualacceptance |
| Интеграции | Numericretry/mailguard; provider-neutral mockDocumentExchange one_c/diadok,13contractsPASS | Конкретныеconfig/vendorformats/testcontour; реальногоAPIсовместимости нет |

## 6. Приоритеты следующего разработчика

### Открытые P0/P1 и закрытый offline logout

Открытые программные пункты требуют реализации и новых проверок. Закрытые ниже имеют отдельные доказательства:

| Приоритет | Задача и исходники | Доказательство | Исполнение/приёмка |
|---|---|---|---|
| Закрыт: offline logout | `frontend/src/auth/localSignout.ts`, `App.tsx`, `auth/Login.tsx`, `api/client.ts`; `backend/app/logout_revocation.py`, `api/auth.py`, `core/auth.py` |877frontend PASS,100E2E matrix, mounted App privacy regression,4actualChromium checks,27targetedauth | Локальная fence и session epoch, durable revoke-only queue, no-store, очистка приватного состояния, предупреждение и сохранение unsent. HttpOnly физически офлайн недоступна; остаточная cookie после server revoke401. Python matrix4227PASS/132SKIP доступна; непроверенные контуры не приняты. |
| Закрыт bounded fallback2f3bc40d | Короткая нулевая координата0e-10000 ранее разворачивалась10002chars до storage. backend/app/drawing_points.py, backend/tests/test_drawing_points.py | STAGE3-POINT-ZERO-EXPONENT-RED JSON04.11; bounded pure probe; большие степени не выполнялись | DS54неотправлен: файлов нет на86a4. Исправлено Codexfallback2f3bc40d: canonicalzero доformat;RED4fail→GREEN28PASS, безмиграции. НовыйHTTP/PG не прогонялся. |
| P1 | Authenticated фото разрешено cache3600sec. backend/app/api/defects.py, backend/tests/test_defects.py | STAGE3-PHOTO-CACHE-DIRECT JSON04.11: current headers,403before lookup,missing404 | DS53request878f910f446280d8206efea961d25b9c REJECTED Invalid bounded request; повтор не выполнен, modeldiff отсутствует. Нужен no-store/nosniff+регрессии. Browser cross-user disclosure НЕ доказано. |

Файлы доказательств находятся в docs/operations/, подробные контракты STAGE3-*-GAP/REVIEW в docs/. Наличие callback/unit probe не заменяет mounted browser→HTTP/PG проверку. Stage3doing; реальная камера/PWA OS install отдельно. После устранения этих P0 перейти к числовымDS39–44 и RD read/viewer. Не повторять неизменённые suites вместо нового результата.


1. Следовать NEXT и последним прямым указаниям пользователя; известные независимые gaps не превращать в ожидание данных. Перед работой проверить owner/pause/gitstatus.
2. P0 текущего review: получить/review DS39–42 и закрыть две воспроизведённые ошибки округления, затем targeted тесты с новым head. Независимо остаётся viewer/сравнение сохранённых PDF/изображений. Не выдавать все remaining tasks за внешние блокеры.
3. Неизменённые зелёные тесты не гонять циклически. Новые локальные правки требуют targeted regression; новыйhead/миграция — своего fresh/filled/parity/restore/concurrency evidence.
4. Production/deploy/releaseapproval/secret/.env/systemservices/bots запрещены. Image/delivery/fullremoteCI и конкретныйproductionrelease не объявлены готовыми.

## 7. Решения и отвергнутые подходы

DECISIONS.md: каждыйпункт — «предварительное решение, требует подтверждения». Strictinferredprice rejectambiguous; explicit opt-inhalf-up, historicaltotal не переписывать; default nonegativestock, explicitconsumption/storno, journalhistoryrestrict; logout: сохранять actor-scoped unsent, удалять owned cache и приватное UI-состояние; прежний destructive default заменён прямым заданием пользователя; normalizedimmutablepoint+disableoption. Unknownconfigfailclosed, .envнечитать.

КСизфакта/ценсметы, snapshot и ограничениеостатком. Оплатанефизическийприход, количествоработынеавтосписание. Версияакта — явнаяколонка/SQLCAS, неauditID. Повторнеподтверждённойкоманды используетисходныйid/bytes; успешныйworkflow/PRнеприёмка. FullparentPRповерхкандидата не накладывать. Нельзя обходить техническийотказ черездругойtransport/пользователя; новыйstand выполнялся штатнымauthorizedtool после нового прямогодопуска.

## 8. Долги, риски, интеграции

Productionцеликом не проверялся/не менялся. РеальныеOAuth/mail/storage/Диадок/1С/FCM/боты здесь не активировались. Секреты не читались; указанытолькоenvnames§4. По переданномустатусу DEEPSEEK_API_KEY — GitHubActionsSecret, несервер; содержимогоключанетвпакете. Limitedpu-release описанпользователем, новыйrelease не разрешён. Credential/rootkeys/DBdumpproduction вsourceне входят.

ActualprivatePGbackuprestore выявил equivalenttextcasts уpartialindex послеpg_dump; 16truth-table случаев включаяNULL подтверждаютэквивалентность, остальнаяmetadata/data136tables и104sequences точны. Это не универсальное доказательствоACL/functions/triggers. Warnings: SQLAlchemycycle organizations/documents/projects, Alembicpath_separator, TestClienthttpx deprecation, mainJSchunk>500kB. Они сохранены, не спрятаны.

РеальныеPDF/printing/phone не подменять syntheticfactfixtures. Offline фото новых замечаний проверены на синтетике; legacy detail photo upload не объявлен durable offline. Codeпрототипы/синтетическиеартефакты не подписывать и не направлятьзаказчикам.

## 9. Последние изменения

Offline logout, Codex fallback: c999232 capability-only session revoke;5750868 durable local fence/unsent retention;4904ff8 actualHTTP regressions;16332d6 API adapter/fixtures;b956f80 server signout marker/BFCache/no-store;677e2b8 full private React state reset;d11a411 direct handler fixtures, explicit offline migration preflight refusal and current applicationCI schema literals. Runtime миграции/службы/production не запускались. fd7f54b5:2mounted logout E2E PASS; e9ce1af3: закрыта post-JSON race18targetedPASS;ee0f3a10:14offlineSQLPASS после исправления downgrade. Полный frontend877PASS; Playwright100 matrixPASS после17регрессий. d8f73041 синхронизирует CI schema pins/AST contracts; b894d194 исправляет ширину offline-баннера, сценарий reading имеет отдельный90s budget с прежними строгими assertions. Актуальные результаты в NEXT.

История предыдущего candidate:

Послеee83e373: ac8f26c(strictpricepolicy); f3e8e2a(stockledger/pointAPI/c4);51e5d02(logoutscope/sessioncleanup);29435b6(mockexchange);651f4ef(syntheticforms);da343e8(historicalunitreplay/HTTPauth);9f75718(typedHTTP/headlesscomponentfixtures);c517215(photoquota parentlock);254a0ad(legacyprice read). Всёэто Codexfallback, локально, непубликовалось.

Broad522backend/3excluded, frontend824, typedHTTP7/otherfocused runs, actualPGc4 иheadless8reports вdocs/operations, этапные выводы вWORK-STATUS/evidence. ПредварительныеRED/harnessfails сохранены/объяснены: NODE_ENV/UTF8/connectivitymonitor testsetup и nondeterministicTCPreset; итоговый HEADLESS-COMPONENTS-VERIFIEDpassed. Не считать старыеRED актуальнойкраснойCI и не выдавать их зазеленые.

PR425–430 ранее exactreviewed черезread-onlyGitHub:430 scopedpositive,429/425/427 defects/testfixturefindings,428newHTTPтесты тогданеrun,426obsolete docs. Ничегоавтоматическинеmerged. DS38queuedбезподтверждённогоrun/PR, не перепосылать.

## 10. Первые шаги

Прочитать NEXT/AGENTS/MASTER/DECISIONS/control, проверить единственногоowner иpause. Восстановитьcurrentpublicsource вновыйкаталог поmanifest/hash, сохранить rootdiff/untracked. Продолжать конкретные независимые пунктыизNEXT сtests/evidence/status, не только описывать планы. Manualреальныепроверки — docs/MANUAL-ACCEPTANCE-2026-11-04.md. Оценки60рабочихдней — исходнаяоценка, не обещаниезавершённости.


### Обновление 04.11: последние точные изменения

- da00fd0a — physical stock UI/history, expected actor, запрет смешения единиц.
-7e92c684 — scoped version picker + immutable point UI.
-f7e79a2c — durable photo receipts/tombstones и c5 migration.
-b7cf8b3b — bounded offline photo blobs;860frontend/119files,headless9checks; оба ownbrowser/server закрыты.
-57b1e146 — **интеграция настоящего DeepSeek PR367**, не новая реализация Codex. Проверены исходные blob c1ef5895fc3da3164f218c5bb3a88e499a4be77b/735316c11fe73626dfe90fe746bec79a7e51aef6. Новая локальная _apply_math сохранена дословно. PR367-INTEGRATION-PROVENANCE/RED/GREEN и patch в docs/operations.

DS39code/DS40tests:0a0c8b3fc888e2c52d9c1dc0187ea81a/574ddda311c9d575e9d8ba00678d1e2f; DS41code/DS42tests:473f5d0c66f860e2c439e29f6f02f320/1c076d34266d5e15c591d879c2f3a2cf. Только localqueued, не modelrun/PR. Исторические source_ref выбраны из уже проверенных опубликованных heads, поскольку локальный57b1e146 не опубликован. Нельзя накладывать весь будущий parent diff/старые файлы: применять только проверенные узкие hunks с сохранением новой policy.

Настройки предварительных решений: PU_ESTIMATE_ROUNDING_POLICY,PU_NEGATIVE_STOCK_POLICY,PU_WORK_LOG_DELETE_POLICY,PU_DRAWING_POINT_POLICY,VITE_OFFLINE_LOGOUT_PURGE. Никаких настоящих .env/секретов не читать.


### Дополнение: числовой render — 04.11.2026

Независимый bounded probe выявил ещё один открытый resource GAP: короткие конечные Decimal1e10000/1e-10000 разворачиваются в10001/10002символа до storage guard. Большие степени не запускались. DS43code1268c006bda7aff44022c8cabb624a62 и DS44tests8584e9e675e887a7984c70be0289f8ab реально localqueued; modelrun/diff пока отсутствуют. Stage1-finite blocked на результаты DS39–44, а не пользовательское разрешение. Карта границ и критерии review: docs/STAGE1-NUMERIC-REVIEW-GATES-2026-11-04.md. Current source57b1e146 не изменён этим review.


### Уточнение DS38 и PDF stand — 04.11.2026

DS38 получил actualrun41795991260: Generate bounded source changes failure, PR skipped; старые утверждения localqueued исторические. Read-only job metadata в DS38-FAILED-GENERATION-2026-11-04.json; workflow не перезапускался и не менялся. DS45–48 новые localqueued задачи read API/native PDF renderer и тестов, не готовая реализация. Rootless Poppler package SHA проверен в/tmp, но libpoppler134 download2proxytimeouts и missing libraries оставили nativePDF smokeUNRUN. См. PDF-RENDER-CONTRACT-2026-11-04.md; viewer/PDF API wiring ещё независимые программные задачи, production не менять.


### Дополнительная приёмка и39строк — 04.11.2026

Текущий ACCEPTANCE-CURRENT-2026-11-04.md сверяет все39исходных строк без полногоPASS. Existing tests на57:87ID/RD/HTTP,34scan/purchase,111storage/mock,2storageHTTP=234unique cases в этих4reports,65subset не прибавлять. SDK3packages установлен только/tmpvenv безOAuthconfig/keys; исходный5missingSDKfailures сохранён. Полный app.main protocol registrationUNRUN, physical printing/normative suitability не приняты. Следующий независимый A36invoice duplicate pipeline, не ждать реальных данных для доступных service checks.

Точка00bc7ad7 добавляет Codex fallback A36: серверный запрет PATCH повторного счёта,48 сервисных +1HTTP PASS на синтетике. Чистое offline восстановление1357файлов подтверждено P1-POST-HANDOFF-2f3bc40d-RESTORE-2026-11-04.json; остальные проверки сохраняют исторические source heads. DS38 фактически завершился failure на генерации, а не queued; см. DS38-FAILED-GENERATION-2026-11-04.json.

Current2f3bc40d — Codexfallbackzero coordinate; существующий перенос00bc7ad7 историческипредыдущий и этогоfix не содержит. Patch/provenance/RED/GREEN вdocs/operations/STAGE3-ZERO-CANONICAL*.

Актуальный перенос2f3bc40d готов:1357files/895PythonAST, SHA256 e9d432405b40ca98fd12bb4927620e69b5afa570f9285658578f07827ce787be, actualoffline restore Gitclean/11728skip/noremotes. Предыдущие00bc/57пакеты сохранены какисторические; remarks выше об ожидании2fпереноса устарели. Companioninstructions/status/evidence передавать отдельно от committedsource.

На2f3bc40d actualbrowserHTTP auth/logout проверен3checks: CSRF403 сохраняетсессию;validlogoutудаляетSQLsession+обеbrowsercookies;потомauth/me401. ReportSTAGE3-BROWSER-ASGI-LOGOUT-2026-11-04.json. OfflinefrontendP0 остаётсяоткрытым, backendonlineproof не закрываетего. DS53rejected,неqueued;historicalqueueevidence сохранён.
