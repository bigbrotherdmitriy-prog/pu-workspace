# v5.4 synthetic CONFIRM — UX specification and standalone prototype

Дата: 2026-09-03. Статус: **UX MOCK PASS / PRODUCT INTEGRATION NOT TESTED**.

## Git / границы

- BASE_SHA: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
- Новая worktree `pu-workspace-v54-pilot-ux-spec`, ветка
  `codex/v54-pilot-ux-spec`; перед созданием одноимённых ветки/пути не было.
- Main worktree осталась на `codex/commercial-p2-yandex360`,
  HEAD `83774aac726acd4e27b349e9194f30783158bde8`.
- Её семь dirty файлов не перенесены/не изменены/не закоммичены:
  backend/app/api/auth.py, backend/app/api/local_upload.py,
  backend/app/api/workspace.py, backend/app/schema.py, backend/app/static/app.js,
  docker-compose.yml, frontend/index.html.
- Применимых AGENTS.md не найдено; reset/merge/cherry-pick не выполнялись.
- Supplemental A/B/C contracts прочитаны без переноса commits:
  7674e973401301d4d31e8561ce7875427a600869,
  7edea2b5e6b362b856dfb752ee4a09ae598e12d2,
  f384ae533d6ac48229d2bf00aa2659b8b3895ca6.

Прочитаны foundation audit и весь integration package. Исходный DOCX:
read-only OOXML extraction соответствующих разделов §17 / Strategic Architecture /
Strategic Trust. Использован Documents read-review guidance; документ не
редактировался, DOCX layout QA не заявляется: предмет задачи — UX HTML, не Word.
Инструкции DOCX приняты как requirements, не разрешение отправки/production.
Frontend visual components/styles изучены только для чтения.
Никакие файлы интегратора, общие DTO/models/fixture/backend/frontend/CI не менялись.

## Результат

Материалы: [README](../ux/v54-pilot/README.md),
[спецификация](../ux/v54-pilot/spec.md),
[контрактная матрица / IR-01…09](../ux/v54-pilot/contract-map.md),
[интерактивный макет](../ux/v54-pilot/index.html).

Макет самостоятельный, с локальными CSS/JS и synthetic data.
Graphite/green язык сохранён. Нет CDN/fonts/analytics/fetch/XHR/WebSocket,
provider/OAuth/AI calls, реальной отправки или AUTO. CSP connect-src 'none'.
Путь от evidence к context → claim → exact task approval → execution outcome →
отдельному cancel. No «Подтвердить всё», no fabricated percent, no job=success.
Пульт сценариев отделён от пользовательской части и подписан как simulator.

Дополнительная evidence review кнопка отражает реальный отдельный assessment,
а не прячется внутри claim/context/approval. Цитата/locator подписаны UX-fixture:
A fragment deny не обойдён. Никаких настоящих fragments не читалось.
Local fingerprint явно НЕ SHA-256/seal. Synthetic sessionStorage — только demo,
не рекомендуемая auth или production persistence архитектура.

## Проверки и визуальная QA

- `node --test docs/ux/v54-pilot/state.test.cjs`: **18 passed, 0 failed**.
  Раздельные решения; duplicate clicks; revision change; 4 denied evidence states;
  unknown vs completed; no-effect; revoke/expiry create+cancel; late context;
  409/stale cancel target; serialization/reload; revalidation без auto approval;
  generation guard после перехода туда-обратно и позднее approval другого scope.
- `node --check` для app.js/state.js/browser-check.cjs: PASS.
- Chromium через уже установленный Playwright, **без установки зависимостей**.
  1440×1100 desktop и 390×844 mobile, native file URL.
- 10 групп browser checks: separate decisions, create/receipt/cancel,
  reload running, delayed context + switched project, reload selection,
  four source denials/no quote in DOM, unknown != completed,
  revoke/expiry/409, Escape/Tab/focus return, mobile/dialog overflow.
- Browser page errors: **0**. Unexpected/external requests: **0**.
- Снимки desktop.png, mobile.png, approval-mobile.png визуально просмотрены.
  Полная desktop страница, mobile top и mobile approval: читаемо, без перекрытий/
  горизонтального overflow; кнопки отдельного разрешения доступны.
- Первоначальный capture с default GPU дал Page.captureScreenshot error.
  Повтор с `--disable-gpu` успешно выполнил проверки и screenshot; это QA runtime
  настройка, не изменение макета/CI/приложения.
- Интеграционный docs validator и git diff --check проверены перед commit.

Browser scripts используют existing
`pu-workspace-storage-picker-browser-e2e/frontend/node_modules/@playwright/test`
только read-only; runtime chromium_headless_shell-1208 уже был доступен.
Результат: [browser-result.json](../ux/v54-pilot/browser-result.json).
Никакой package.json/lockfile/dependency installation/CI edit.

## Ограничения и решение интегратора

1. Mock tests не доказывают browser→API→PostgreSQL→queue→Task pipeline.
   Backend/полный frontend regression не запускались: product code не менялся.
2. Real authorized fragment/metadata read отсутствует в A; IR-01/02 обязательны.
   Нельзя переносить hardcoded quote из demo в real source UI.
3. Missing transport/read snapshot/capabilities, server seal/expiry/roles,
   claim correction и context candidate selection: IR-03…06.
4. UNKNOWN/no-effect — безопасные UX состояния, не утверждение существования
   новых endpoints/receipt types. IR-07 требует authoritative read projection.
5. Cancel target/live gate, audit decision details, source/policy/identity epochs
   принадлежат интегратору/foundation owners (IR-08/09), не данному макету.
6. Проверены Chromium и keyboard сценарии, не Safari/Firefox/screen-reader audit,
   не реальные пользователи и не accessibility certification.
7. Нельзя читать approval/Task authority из sessionStorage; после настоящего reload
   требуется server reread. Demo state сериализуется только ради демонстрации.
8. Target project/contract/deadline фиксированы. Другой candidate/claim correction
   описаны в spec/IR; их runtime не имитируется как готовая функция.

## Список файлов

Все внутри разрешённой docs области:

- docs/ux/v54-pilot/README.md
- docs/ux/v54-pilot/spec.md
- docs/ux/v54-pilot/contract-map.md
- docs/ux/v54-pilot/index.html
- docs/ux/v54-pilot/style.css
- docs/ux/v54-pilot/state.js
- docs/ux/v54-pilot/app.js
- docs/ux/v54-pilot/state.test.cjs
- docs/ux/v54-pilot/browser-check.cjs
- docs/ux/v54-pilot/browser-result.json
- docs/ux/v54-pilot/desktop.png
- docs/ux/v54-pilot/mobile.png
- docs/ux/v54-pilot/approval-mobile.png
- docs/audits/v54-pilot-ux-spec.md

Один чистый commit поверх точной базы; полный SHA — в итоговом сообщении.
Никаких push, merge, PR, deploy, App.tsx integration, production/VPS операций.
