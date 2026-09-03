# Storage picker UI validation

Дата: 2026-09-03. Ветка: `codex/storage-picker-ui-validation`.
Worktree: `C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/pu-workspace-storage-picker-ui-validation`.
Точная база: `814ff77b79bd3a6d1382c345783946a7b9b7898e`.

## Зависимость и границы коммита

Перед UI-работой выполнен подготовительный cherry-pick `387c75019df040d8fc8166457b3d1970a975b835`. Его локальный SHA: `65e05d0f9cbd2048b4312386185ad5c56928ea23`.

**Интегратору переносить только следующий, отдельный UI-коммит. Не повторять cherry-pick storage.** Полный SHA UI-коммита указан в итоговом сообщении; для этой ветки его также возвращает `git log -1 --format=%H -- docs/audits/storage-picker-ui-validation.md`.

Основная worktree осталась на `83774aac726acd4e27b349e9194f30783158bde8`, `codex/commercial-p2-yandex360`. Не перенесены и не изменены пользовательские правки: `backend/app/api/auth.py`, `backend/app/api/local_upload.py`, `backend/app/api/workspace.py`, `backend/app/schema.py`, `backend/app/static/app.js`, `docker-compose.yml`, `frontend/index.html`.

В целевой worktree и применимых родительских каталогах AGENTS.md не обнаружены. Изучен `storage-binding-validation.md`, фактические discovery/confirmation handlers, App.tsx, useProjectSelection и существующие React/Vitest-тесты.

## Аудит

Исходный App применял discovery без проверки актуальности запроса, на подтверждении заново брал текущий project ref, предварительно сбрасывал root Яндекса и вставлял folder.id в URL без encoding. Позднее подтверждение вызывало `load(targetProjectId)`. Отсутствующий requested project молча заменялся первым проектом списка. Прогресс содержал придуманные значения 5%/10% и восстановление количества обработанных объектов по проценту.

## Что изменено

- Состояние выбора вынесено в `useStoragePicker`, подключённый к настоящему App. На открытии фиксируются project/provider/connection_id/connection_row_id из discovery. Epoch окна, номер discovery и текущий project ref определяют допустимость ответа.
- Смена проекта, закрытие, новое открытие/подключение и новый discovery инвалидируют старую работу. Ответы со старым контекстом не обновляют видимые данные. Navigation сверяет все четыре поля binding.
- Confirmation использует только project picker и передаёт поддерживаемые `provider`/не-null `connection_id`. `connection_row_id` сравнивается в ответе, но не выдаётся за серверный guard.
- Google opaque ID и путь Яндекса передаются через `encodeURIComponent`. Query navigation строится через URLSearchParams. Пробелы, кириллица, `#`, `?`, `%`, вложенные `/` проверены тестом.
- Открытие не выполняет PUT и не меняет root. Discovery без folder_id восстанавливает сохранённую папку. Конкретный provider в кнопке передаётся guard-параметром: чужой provider не открывается молча.
- Проверяется binding ответа confirmation и folder_id. Для OAuth-only Google допускается только предусмотренный backend переход connection_row_id=null -> новый ID при том же project/provider/connection_id.
- Подтверждённые snapshot/job IDs сохраняются под ключом `pu_storage_selection_v1:<project_id>` в sessionStorage. Поздний корректный ответ может сохранить результат **только для своего проекта**, но не открывает окно, не меняет активный проект и не вызывает load старого проекта. Более старое подтверждение не перезаписывает результат более нового в том же экземпляре hook.
- Добавлены кнопка выбора текущей папки и отображение конкретного снимка/задания/статуса. Reload/remount восстанавливает запись проекта; discovery дополнительно запрашивает `/projects/{id}/snapshots` для актуализации статуса сохранённого снимка.
- Ошибка 409 очищает устаревший confirmable context и предлагает «Переоткрыть выбор». Автоперепривязки нет. Для null connection ID явно показано ограничение проверки аккаунта.
- `requestedProjectId` запрещает fallback при явно выбранном/восстановленном ненулевом ID. App сохраняет этот ID и показывает ошибку/placeholder в select. Первый проект выбирается только при отсутствии любого заданного ID.
- Связанные primary/analysis действия используют capture picker и проверяют его перед применением результата. Processing-queue polling не применяет ответ другого проекта.
- Удалены искусственные проценты, вычисленная локально позиция очереди и фиктивное количество обработанных объектов. При отсутствии серверного progress отображается неопределённое состояние. Яндекс-источник больше не получает ссылку «Открыть в Google Drive».

## Проверки

Синтетический mock API; реальные Google/Яндекс, OAuth, файлы и внешние AI не вызывались. Auth client не изменялся и не обходился. API mock применяется только в тестах.

`useStoragePicker.test.tsx`: 19 поведенческих тестов hook и project selection:

- вложенные папки обоих providers, guards, спецсимволы и отсутствие root-setting PUT;
- смена проекта до rerender, обратный порядок ответов, закрытие/смена provider во время discovery;
- позднее подтверждение после смены проекта, отказ отправлять выбор от старого picker;
- несовпадение project/provider/connection/row и ошибки 409;
- remount, сохранённые job/snapshot и реальный failed-статус из snapshots;
- null connection и создание Google connection row;
- новый проект рядом с первым Persistent Project, сохранение выбора и отказ от fallback.

`StoragePickerApp.test.tsx`: 3 поведенческих теста **настоящего App**:

- отсутствующий новый проект визуально остаётся выбранным, появляется понятная ошибка;
- выбор текущей папки, смена проекта во время confirmation, поздний ответ не вызывает загрузку прежнего проекта;
- building-снимок без measurements не показывает 5% или 10%.

Финальные команды и результаты:

| Проверка | Результат |
|---|---|
| `pnpm install --offline --frozen-lockfile` | Успешно; lockfile не изменён, скачиваний нет |
| `pnpm run check` | Успешно |
| `pnpm run test` | **8 файлов, 39 тестов passed** |
| `pnpm run build --outDir <новый временный каталог>` | Успешно, 1616 модулей; JS 440.51 kB / gzip 128.05 kB |
| `git diff --check` | Без ошибок |

Первый запуск Vitest был заблокирован sandbox при загрузке esbuild config; повтор с разрешённым доступом прошёл. Существующий build script по умолчанию пишет отслеживаемый `backend/app/react_dist`: результаты первого build восстановлены к HEAD, новый bundle удалён. Финальный build направлен во вновь созданный временный каталог; backend diff отсутствует.

Live-browser проверка недоступна: CUA не смог инициализировать kernel assets (`os error 3`). Browser E2E и реальные provider-сессии **не считаются проверенными**. JSDOM/Vitest не заменяет браузерную проверку.

## Оставшиеся backend-зависимости

1. Discovery работает только с уже выбранным provider. Нет независимого browse конкретной авторизованной connection до изменения root. Если выбран Google, кнопка Яндекса получает понятный 409 вместо скрытого PUT на disk:/. Нужен отдельный backend-контракт выбора/browse подключения, сохраняющий существующий root до явного подтверждения. В UI-коммите новый endpoint не выдумывается.
2. `connection_row_id` не принимается сервером как guard; `connection_id=null` и смена account внутри той же credential row остаются неполностью защищёнными. Нужна версия connection и атомарная server-side проверка. Проверка ответа UI не может отменить уже совершённую серверную запись.
3. Точный промежуточный процент snapshot/job, кооперативная отмена, согласование retry-build с автоматическим worker retry остаются задачей backend/очереди. UI показывает только полученные числа/статусы.
4. Повтор confirmation идемпотентен и не пересканирует ready-снимок. Кнопка переименована в «Выбрать эту папку», не обещает «Найти новые файлы». Для failed snapshot показан сохранённый результат; explicit retry остаётся в существующей панели очереди/настроек.
5. GET snapshots не возвращает job_id и подробный build error. Job ID восстанавливается из sessionStorage только если этот клиент получил confirmation. Для восстановления на другом устройстве/после очистки sessionStorage нужен backend endpoint связи snapshot/job и полной безопасной ошибки. Cache — последний подтверждённый ответ; следующий discovery обновляет status.
6. Limits backend (255 символов locator, 100 шагов ancestry) и legacy snapshots без pinned binding остаются из storage-аудита. UI их не скрывает и не заявляет безлимитную глубину.

Сценарий для уже выбранного корректно настроенного подключения реализован и покрыт mock-тестами. Для произвольной смены provider/account и live-browser приёмки остаются перечисленные зависимости; весь end-to-end production сценарий не объявляется завершённым.

## Файлы UI-коммита

```text
frontend/src/App.tsx
frontend/src/context/useProjectSelection.ts
frontend/src/modules/integrations/useStoragePicker.ts
frontend/src/modules/integrations/useStoragePicker.test.tsx
frontend/src/modules/integrations/StoragePickerApp.test.tsx
docs/audits/storage-picker-ui-validation.md
```

Backend, очередь, миграции, CI, Compose, static/app.js и legal в UI-коммит не входят. Push, merge и deploy не выполнялись.
