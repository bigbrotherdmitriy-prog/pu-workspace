# Storage binding validation

Дата: 2026-09-03. Ветка: `codex/storage-binding-validation`.
Точная база: `814ff77b79bd3a6d1382c345783946a7b9b7898e`.
Worktree: `C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/pu-workspace-storage-binding-validation`.

## Статус

Backend-сценарий подтверждения папки проверен синтетическими HTTP-тестами для Google Drive и Яндекс Диска. Пользовательский сценарий **не завершён целиком**: интеграция picker в App.tsx требует отдельного изменения. Реальные provider API, браузер и PostgreSQL в этой задаче не запускались.

Основная worktree оставлена на `83774aac726acd4e27b349e9194f30783158bde8`, ветка `codex/commercial-p2-yandex360`. При старте обнаружены и не перенесены изменения:

```text
backend/app/api/auth.py
backend/app/api/local_upload.py
backend/app/api/workspace.py
backend/app/schema.py
backend/app/static/app.js
docker-compose.yml
frontend/index.html
```

Применимые AGENTS.md в родительских каталогах и в целевой worktree не найдены. Существующие ветки/worktree не сбрасывались.

## Краткий аудит и путь запроса

1. `IntegrationsModule.onSelectFolder(provider)` вызывает `App.tsx:openProviderSources`.
2. `openSources` обращается к `GET /projects/{project_id}/source-folders/discover?folder_id=...`. Provider определяется сохранённым DriveConnection, а не названием кнопки.
3. `queueFolder` подтверждает через `POST /projects/{project_id}/source-folders/{external_id:path}/snapshot-queue`. Для Яндекса UI предварительно вызывает `/yandex/root`.
4. Workspace сохраняет SourceFolder и WorkspaceSnapshot, ставит `workspace.snapshot` в существующую durable очередь.
5. Неизменённый `jobs/handlers.py` вызывает `_build_snapshot`, затем создаётся `workspace.safe_copy`; его handler вызывает `_run_safe_copy_pipeline` и существующий organizer.
6. UI получает состояние через `/snapshots` и `/processing-queue`. Это не серверный active-project: выбор активного проекта хранится клиентом в `pu_active_project_id`.

Adapters уже поддерживали рекурсивный обход. Ошибки обнаружены в фиксации выбора, связи async-операции с исходным контекстом, повторных запросах и namespace breadcrumb.

## Подтверждённые дефекты и исправления

- Подтверждение возвращало только ID снимка и статус; Google root не сохранялся. Теперь ответ содержит `project_id`, `provider`, `connection_id`, `connection_row_id`, `folder_id`, `job_id`. Выбранный root и его имя сохраняются в DriveConnection.
- Повтор после `ready` создавал новый снимок. Подтверждение теперь возвращает последний существующий снимок; для ошибки нужен явный `retry-build`, а не повторное создание. Повторное подтверждение также восстанавливает выбранный root после выбора другой папки.
- Снимок коммитился раньше enqueue. Для первого подтверждения binding, SourceFolder, WorkspaceSnapshot и job теперь коммитятся существующей `enqueue(db, ...)` в одной транзакции. Аналогично устранена отдельная фиксация organizer session до enqueue safe-copy.
- Worker мог принять snapshot одного проекта и project_id другого. Добавлена проверка snapshot/project/source до provider-вызовов и до записи ошибки в чужой снимок; safe-copy дополнительно сверяет organizer session.
- Смена connection между подтверждением и выполнением не обнаруживалась. Точный binding фиксируется в существующем JSON `WorkspaceSnapshot.analysis_result.storage_binding`, сохраняется при записи результатов/перезапуске анализа и проверяется в worker. Изменение connection приводит к явной ошибке, без выбора другого аккаунта.
- Discovery по умолчанию возвращал корень аккаунта вместо сохранённой папки. Без `folder_id` теперь открывается выбранная папка; явный переход в родителя/корень остаётся отдельным запросом.
- Opaque Google ID и Yandex path принимались без разграничения. Подтверждение Google принимает opaque ID; Яндекс требует `disk:/...` либо `app:/...`, без преобразования чужого идентификатора в путь. Относительные пути/resource_id в этом endpoint не принимаются.
- Для `app:/A/B/C` Яндекс формировал `disk:app:/A/B`. Namespace родителя и breadcrumb исправлены. Циклы/превышение лимита ancestry завершаются явной ошибкой.
- Yandex resolver игнорировал `DriveConnection.connection_id`. Если он указан, теперь проверяется совпадение с project-scoped IntegrationCredential; disconnected connection не используется.
- После сохранения ready-снимка сбой перед enqueue анализа оставлял снимок без анализа: повторный handler раньше сразу выходил. Теперь ready-снимок повторно не строится, но идемпотентная постановка safe-copy восстанавливается.
- Исключение построения снимка сохранялось через `str(exc)`, включая потенциально чувствительные детали. Пользовательское поле ошибки построения/виртуального анализа теперь содержит фиксированное безопасное сообщение.

Совместимость Google OAuth: callback в базе сохраняет только GoogleOAuthToken. При отсутствии DriveConnection разрешено read-only просмотреть **только авторизованный текущий проект**, а на подтверждении создать connection с `connection_id=google-token:<id>`. Токены другого проекта не ищутся. У существующих Google connections и credentials значения не переписываются.

## Проверки

Перед первым исправлением новый regression-набор дал **14 failed**: отсутствие binding/job в ответе, дубли после ready, неверный project worker, stale connection, путаница locator, восстановление выбранной папки, namespace app и проверка credential ID.

Дополнительные падающие проверки до соответствующих исправлений: **8 failed** на повторном выборе сохранённого root, сбое после ready, orphan session при enqueue failure и потере binding при явном анализе. Отдельно пойман и исправлен compatibility-regression OAuth-only Google project (1 failed).

Используются SQLite в отдельном временном файле на тест, FastAPI TestClient, fake storage adapters и httpx.MockTransport существующих adapter-тестов. OrganizerRepository.create_session в fixture заменён ORM-вставкой, поскольку SQLite create_all не воспроизводит PostgreSQL server defaults миграций. Постановка BackgroundJob и dispatch `jobs.handlers.run` используются настоящие; файловый анализ/safe-copy завершается fake scanner, без чтения документов, отправок сообщений и AI.

| Требование | Проверка / граница доказательства |
|---|---|
| Новый проект рядом с Persistent Project | Оба существуют; HTTP и job адресованы новому, старый не изменяется |
| Верхняя папка, >=3 уровня, одинаковые имена | Параметризованная навигация и два разных locator с одинаковым названием |
| project/provider/connection/folder | Проверяются БД, ответ подтверждения, snapshot binding и job payload |
| Непрозрачные ID против путей | Чужой формат отклоняется до adapter; спецсимволы передаются через URL encoding |
| Breadcrumb и родитель | Google fake tree, Яндекс disk/app namespace; возврат по ID родителя |
| Повтор HTTP | Один снимок и job при повторном запросе, включая ready и failed |
| Восстановление | Новый HTTP-запрос/DB session восстанавливает root и снимок; browser session отдельно не прогонялась |
| Async завершение | Реальный dispatch snapshot/safe-copy, fake scanner, результат остаётся в новом проекте |
| Чужой пользователь/проект/connection | 403/404/409, старые credentials не используются |
| Durable analysis | Проверены enqueue snapshot -> enqueue safe-copy -> dispatch; handlers не изменены |
| Прогресс/завершение | Настоящие claim/succeed: job 1 -> 100; processing-queue отражает scanner progress 100 |
| Ошибка и retry | Безопасная ошибка, явный retry с исходным locator; stale connection retry запрещён |
| Потеря enqueue | Rollback первого подтверждения/session; повтор ready-job восстанавливает follow-up |
| Отсутствие возврата к старому проекту | Доказано для backend; клиентские гонки ещё требуют подключения UI |

Команда целевого regression-прогона (из backend, с тестовой `DATABASE_URL=sqlite+pysqlite:///:memory:`):

```text
python -m pytest tests/test_storage_binding_validation.py tests/test_storage_provider_regression.py tests/test_yandex_storage_adapter_contract.py tests/test_storage_adapter_contract_matrix.py tests/test_virtual_workspace_api.py tests/test_job_hardening_contract.py tests/test_project_lifecycle.py tests/test_frontend_project_state.py tests/test_frontend_project_launch.py -q --tb=short -p no:cacheprovider
```

Итоговый результат: **87 passed in 39.45s**. `git diff --check` — без ошибок. Полный backend suite не запускался; целевые существующие regression-тесты входят в указанную команду. Docker/PostgreSQL executables и тестовое подключение PostgreSQL в окружении не обнаружены.

## Точки подключения UI для интегратора (App.tsx не изменён)

- `openProviderSources`, около строки 674 базы: не сбрасывать Яндекс root на `disk:/` ради открытия окна. Кнопка Google при выбранном Яндексе сейчас тоже попадает в Yandex discovery: нужна явная операция выбора авторизованной connection, затем discovery.
- `openSources`, около 1021: сохранить `{project_id, provider, connection_id, connection_row_id}` ответа как контекст picker. Не применять поздний ответ, если активный проект или версия запроса уже изменились. При восстановлении использовать discovery без folder_id; для навигации передавать конкретный breadcrumb ID.
- `queueFolder`, около 1039: использовать project_id **контекста picker**, а не заново читать текущий ref после переключения проекта. Передать query `provider` и `connection_id`; при 409 закрыть устаревший выбор и попросить открыть его заново. Путь формировать через `encodeURIComponent(folder.id)` (сейчас `#`, `?`, `%` могут испортить запрос).
- После подтверждения сверить `response.project_id` с контекстом выбора. Сохранять `job_id`/snapshot ID в состоянии этого проекта. Не применять результат и не вызывать `load(oldId)` после переключения пользователя на другой проект.
- `load`, около 473: fallback к `p.projects[0]` при отсутствии ожидаемого проекта способен выбрать Persistent Project. Для явно начатого нового проекта показывать ошибку/повтор загрузки, не выбирать другой проект автоматически.
- `useProjectSelection.ts` уже сохраняет `pu_active_project_id` и отдаёт приоритет project_id OAuth callback. Browser E2E должен подтвердить новый проект после создания, выбора, reload, смены вкладки и завершения job. В текущих тестах frontend-проверки только статические.

Provider/connection query-параметры пока необязательны для совместимости существующих клиентов. Полная защита от выбора по устаревшему picker требует их подключения UI. Для legacy connection с null `connection_id` потребуется отдельный concurrency/version token; `connection_row_id` уже возвращается, но как query guard пока не принимается.

## Зависимости и ограничения

- Никаких изменений в jobs/handlers.py, App.tsx, static/app.js, OAuth, credentials, Android/local upload, Gmail/AI Secretary, OCR, encrypted staging и production Compose.
- Новые job kinds и новый контракт handlers не нужны для этих изменений. Payload остаётся `{snapshot_id, project_id, external_id}` и `{snapshot_id, session_id, project_id, source_folder_id}`.
- Потоку очереди: нужен отдельный контракт для scoped job progress с `job_id` внутри handler и кооперативной отменой. Текущий snapshot handler не передаёт job_id и не сообщает точный процент обхода. Проверка 1 -> 100 не доказывает промежуточный процент сканирования.
- Потоку очереди: согласовать explicit retry-build с автоматическим retrying/running того же snapshot; существующий force retry создаёт отдельный job. Проверки конкурентных workers/lease на PostgreSQL здесь не выполнены. Нужна сериализация повторного анализа/смены connection и pin credentials непосредственно при получении adapter, чтобы исключить TOCTOU во время reauthorization.
- Существующие неприкреплённые к connection старые/bulk snapshots не получают выдуманный historical binding. Это требует миграционной политики интегратора. Смена credential account внутри той же записи с тем же ID также требует версии connection; текущий pin фиксирует IDs, а не версию секрета.
- Существующий organizer может писать свои provider errors в сессию/уведомление. Эта задача очищает поля workspace snapshot/virtual-analysis; аудит всего organizer logging принадлежит его потоку.
- Модели и миграции не изменены. Additive JSON `storage_binding` должен сохраняться всеми будущими writers `analysis_result`. Для длинных Yandex путей нужен согласованный переход SourceFolder.external_id, DriveConnection.root_folder_id и связанных organizer полей с String(255) на Text/больший лимит, а также уникальность источника по project/provider/connection/locator. Сейчас >255 символов отклоняются 422, ancestry >100 шагов отклоняется явно. Не заявляется безлимитная глубина.
- Реальный PostgreSQL, Google Shared Drives, реальные OAuth-подключения, живой Яндекс, браузерные гонки, реальное чтение/копирование/анализ файлов и полный backend-набор не проверены. Production .env не читался, provider API не вызывались.

Изменяемые файлы: `backend/app/api/workspace.py`, `backend/app/integrations/storage.py`, `backend/app/integrations/yandex_disk.py`, `backend/tests/test_storage_binding_validation.py`, этот отчёт. Push, merge и deploy не выполнялись.
