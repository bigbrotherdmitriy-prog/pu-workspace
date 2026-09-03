# Storage picker: real browser / synthetic API E2E

Дата: 2026-09-03. Результат: **15 browser tests PASS** в локальном Chromium.
Это проверка настоящего собранного frontend, **не** живых Google/Яндекс и не
сквозная проверка browser → backend → PostgreSQL → worker.

## Изоляция и база

- Ветка: `codex/storage-picker-browser-e2e`.
- Точная база: `62b939db82167c51e3fd1b9959c9e904d0d3cede`.
- Новая чистая worktree: `C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/pu-workspace-storage-picker-browser-e2e`.
- Применимые AGENTS.md в worktree/родительских каталогах не обнаружены.
- Прочитаны storage-picker-ui-validation.md, storage-binding-validation.md,
  parallel-validation-final.md; изучены существующие Vitest/App/hook tests,
  API client, discovery/confirmation/snapshots/processing-queue/analyze/standardize
  handlers и ответы связанных экранов.
- Основная worktree не изменялась: `codex/commercial-p2-yandex360`,
  `83774aac726acd4e27b349e9194f30783158bde8`. Её незакоммиченные файлы не
  переносились: backend/app/api/auth.py, local_upload.py, workspace.py;
  backend/app/schema.py; backend/app/static/app.js; docker-compose.yml;
  frontend/index.html.
- Собственный SHA выдаётся в финальном сообщении; после коммита:
  `git log -1 --format=%H -- docs/audits/storage-picker-browser-e2e.md`.

## Реализация и безопасность

Встроенный браузерный инструмент недоступен (`kernel assets`, os error 3).
Добавлен минимальный Playwright 1.58.2 harness; локально установлен Chromium
Headless Shell 145.0.7632.6, build 1208. Используется реальный App/main.tsx,
production Vite bundle, настоящий Chromium, UI clicks и реальные HTTP fetch.
Нет React/hook mocks и нет тестового альтернативного UI.

Отдельная Vite config: `envDir: false`, нет proxy, build только в игнорируемый
`frontend/node_modules/.cache/storage-picker-e2e/site`. Production .env и
credentials не читаются; tracked backend/react_dist не перезаписывается.
Preview слушает только 127.0.0.1:4179, strictPort; чужой сервер не переиспользуется.
Playwright запускает/останавливает свой webServer; 1 worker, retries=0,
test timeout 30 s, global timeout 300 s, server startup timeout 90 s.

Новый browser context на тест, без пользовательского профиля. HTTP имеет
deny-by-default boundary: только локальный bundle/static allowlist проходит
в preview; известные API получают синтетические ответы. Неожиданные API,
внешние запросы и WebSocket блокируются и проваливают тест; service workers
запрещены. Chromium background networking отключён и внешний DNS заблокирован.
Нет OAuth, Gmail sync, AI, реальных документов/Drive writes. Synthetic
`/auth/me` и CSRF cookie не доказывают серверную авторизацию; реальный auth
client не изменён, CSRF header POST проверяется. Изоляция browser routing
не заменяет системный firewall; установка npm/browser/CI инструментов требует сети.

Гонки воспроизводятся удержанием конкретного HTTP-запроса с явным release,
ожиданием response и React paint. Произвольных sleep/waitForTimeout нет.
Для отсутствия retry loop часы браузера управляемо продвигаются на 16 секунд.
Протокол каждого теста содержит только synthetic method/path, неожиданные
запросы и page errors. Секреты, документы и production logs отсутствуют.

## Результаты по каждому сценарию

| № | Сценарий | Результат / доказательство |
|---|---|---|
| 1 | Новый проект рядом с Persistent Project | PASS: выбран ID 2; discovery адресован ID 2 |
| 2 | Открытие выбранного provider без root reset | PASS для обоих: guard provider, folder_id отсутствует, PUT/yandex/root отсутствуют |
| 3 | Вложенные папки обоих providers | PASS: root → заказчик → проект → этап; breadcrumb и переход к родителю |
| 4 | Кириллица, пробелы, #, ?, % | PASS: `disk:/Заказчик/Проект #1/Этап ? 50%`, encoded POST path, query guards и отсутствие URL hash |
| 5 | Discovery replies в обратном порядке | PASS: видимы breadcrumb/дети последнего запроса, старый ответ не заменяет их |
| 6 | Смена проекта до discovery | PASS: выбран новый активный ID 1; поздний ответ ID 2 не открывает picker |
| 7 | Смена проекта до confirmation | PASS: POST остаётся для ID 2; cache job 42/snapshot 31 только проекта 2 |
| 8 | Закрытие/повторное открытие | PASS: новый контекст побеждает старый незавершённый discovery |
| 9 | Старый ответ не возвращает старый проект | PASS: active project не меняется; нет нового load старого проекта после reply |
| 10 | Настоящий page.reload | PASS: ID 2, выбранный Yandex root, job/snapshot восстанавливаются; статус обновляется до ready/failed из API |
| 11 | 409 без retry loop | PASS отдельно discovery/confirmation; понятная ошибка, нет confirmable папки; продолжение только через явное переоткрытие |
| 12 | already_queued != completed | PASS отдельно analyze/standardize; analyzing/retrying не объявляются завершением |
| 13 | Фактический прогресс | PASS: unknown без 5/10%; server 37%, 7 обработано/13 осталось, running отображаются как получены |

Дополнительно: Google-кнопка при выбранном Яндексе получает понятный конфликт,
без скрытой смены provider; отсутствующий явный проект после reload не
заменяется первым Persistent Project. Всего 15 тестов (часть покрывает
несколько связанных требований).

В начальных прогонах исправлены только harness-дефекты: добавлены явные
fixtures GET execution/overview и document-candidates; учтён CSS-разделитель
`›` в accessibility name breadcrumb; после появления снимка кнопка называется
«Все источники»; loading и selection имеют разные элементы role=status.
Эти падения не обходились force-click, sleep, skip или retries.
Продуктовых функциональных дефектов в перечисленных сценариях не обнаружено;
App.tsx/useStoragePicker.ts и весь product code не изменялись. Скриншоты
не являются утверждением о pixel-perfect дизайне: существующие translucency
и hover-tooltip остаются без переработки.

## Выполненные проверки

Окружение: Windows, Node 24.20.0, pnpm 11.19.0, Playwright 1.58.2.

| Команда | Результат |
|---|---|
| `pnpm run check` | PASS |
| `pnpm run check:e2e` | PASS, включая fixtures и Playwright config |
| `pnpm run test` | **44 passed, 8 files**, 8.62 s |
| `pnpm run build --config e2e/vite.config.mjs` | PASS, 1616 modules; JS 441.15 kB / gzip 128.26 kB |
| `pnpm run test:e2e` | **15 passed**, 17.5 s, retries=0; предыдущий полный зелёный прогон 18.6 s |
| actionlint 1.7.12 нового workflow | PASS, exit 0 |
| `git diff --check` | PASS |

Финальные type/build/unit проверки прошли; после уточнения последнего
reload-селектора и контрактов вспомогательных fixtures повторно прошли
check:e2e и полный browser suite.
Единственные предупреждения browser-run — NO_COLOR/FORCE_COLOR.

## Доказательства

Локальный HTML report (не коммитится):
`frontend/node_modules/.cache/storage-picker-e2e/report/index.html`.
SHA-256 этого финального report:
`2b79caa8ddcc91fc31738934bb0b35323ee69de25007ee412015fbc47e5656e9`.
В report вложены synthetic HTTP protocol каждого теста и 3 скриншота:
Google selection, Yandex selection, measured/unknown progress.
Скриншоты выбора обоих провайдеров просмотрены визуально.
Trace сохраняется при падении, а не выдаётся за trace успешного прогона.
Следующий запуск может заменить локальные report/results; CI сохраняет их
artifact `storage-picker-e2e-<run_id>-<run_attempt>` на 7 дней.
Site bundle, node_modules целиком и env-файлы в artifact не включаются.

## Ограничения

- Независимый выбор другого подключения backend не поддерживает; mock этого
  не добавляет. Ошибка выбора чужого provider проверена, смена аккаунта — нет.
- Server-side connection version guard отсутствует; legacy connection_id=null
  и reauth внутри той же credential row остаются не полностью защищены.
- Mock E2E не доказывает совместимость живых Google/Яндекс API, работу OAuth,
  permissions реального сервера, сохранение в PostgreSQL и исполнение jobs.
- Backend handlers сверены вручную, но не запускаются этим harness. При смене
  контракта fixtures требуют синхронизации с backend regression suite.
- Reload проверяется в том же browser session; восстановление job на другом
  устройстве и после очистки sessionStorage требует backend API.
- Проверен desktop Chromium, не Android/mobile/Firefox/WebKit. Linux CI
  workflow подготовлен и linted, но не запускался. Production не проверялся.

## Перенос и запуск

Поверх указанной интегрированной базы:

```sh
git cherry-pick <полный SHA единственного коммита из финального ответа>
cd frontend
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps --only-shell chromium
pnpm run check
pnpm run check:e2e
pnpm run test
pnpm run build --config e2e/vite.config.mjs
pnpm run test:e2e
```

На Windows для установки browser достаточно `playwright install --only-shell chromium`.
Посмотреть локальные доказательства: `pnpm exec playwright show-report node_modules/.cache/storage-picker-e2e/report`.

Для CI нужны отдельное разрешение на push/запуск, GitHub Actions runner
ubuntu-latest и доступ к npm, Chromium CDN, Go modules/GitHub для tooling.
Никакие production secrets, backend или аккаунты providers не нужны.
Workflow: workflow_dispatch; push main/текущей task-ветки и pull_request с
frontend/workflow paths; contents:read, job timeout 15 min, artifact always.
Push другой интеграционной ветки сам по себе этот job не запускает: нужен
разрешённый PR, main push либо workflow_dispatch, доступный в GitHub.
Ни push, ни PR, ни dispatch, ни merge/deploy здесь не выполнялись.

## Файлы

- `.github/workflows/storage-picker-e2e.yml`
- `frontend/package.json`
- `frontend/pnpm-lock.yaml` (только Playwright и его зависимости, без обновления существующих версий)
- `frontend/playwright.config.ts`
- `frontend/e2e/vite.config.mjs`
- `frontend/e2e/tsconfig.json`
- `frontend/e2e/storage-fixtures.ts`
- `frontend/e2e/storage-picker.e2e.ts`
- `docs/audits/storage-picker-browser-e2e.md`
