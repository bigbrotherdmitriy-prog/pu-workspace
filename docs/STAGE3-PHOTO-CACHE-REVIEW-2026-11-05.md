# Этап3: HTTP-кэш фотографий — текущий review

Источник: 00306095c6f341249637478b2071d9ba03d119ae, существующий verified candidate. Код/тесты проекта не изменялись; implementation DeepSeek отсутствует. Review выполнен 2026-11-05T06:26:57.605988+00:00.

## Подтверждено

`download_photo` всё ещё отдаёт `Cache-Control: private, max-age=3600`, без `X-Content-Type-Options: nosniff`. SHA256 api/defects.py: `c0c2c9f4f4f2eb3c9877f7a5aab2bdad6961b8a10b7f185c095de1b02b278465`; test_defects.py: `526ad0ffea9d9f36daf0a15242b56c1fd5e36c80fe0542c843d5cb4b9159d306`. Это отдельный P1 после закрытого offline logout: удаление CacheStorage не очищает HTTP disk cache. Утечка другому пользователю в реальном браузере НЕ воспроизводилась и не заявляется.

Curator probe выполняет actual route callable с synthetic role/service stand-ins: viewer проверяется до чтения, точные project/defect/photoIDs передаются сервису, body/MIME сохраняются; отказ403 до доступа к фото; LookupError404. Три контроля PASS; два критерия privacy(no-store/nosniff) НЕ выполнены. Это direct-call review, не HTTP/browser/session acceptance и не новые проектные tests/fixtures.

Существующие scope/quota/isolation тесты:6PASS на memorySQLite, env-i, запрет network/DNS/subprocess/realenv. XML: `docs/operations/OFFLINE-LOGOUT-BACKEND-PU-PHOTO-CACHE-REVIEW-SELECTION-2026-11-05.xml`. Probe: `docs/operations/STAGE3-PHOTO-CACHE-00306095-2026-11-05.json`. Полный877/100/4227 набор не повторялся: исходники не изменены, новая проверка узкая.

## Уточнённый контракт DeepSeek — подготовлен, НЕ отправлен повторно

Сохранить исходное DS53 `878f910f446280d8206efea961d25b9c`. Read-only pu-deepseek status подтвердил terminal `rejected`, причина `Invalid bounded request`. Модельный run/PR/diff не подтверждён. Запрос не переотправлять, отказ не обходить; изменение dispatcher/runner/validator не входит в задачу. Причина отказа точнее generic reason — не уверен. Публичность текущего локального source SHA не подтверждена; старые полные файлы на86a4 поверх candidate не переносить.

Будущее ограниченное исправление — только заголовки успешного authenticated download в `backend/app/api/defects.py` и небольшие синтетические direct-handler regressions в существующем `backend/tests/test_defects.py`:

1. `Cache-Control` должен содержать private и no-store, не positive max-age; добавить `X-Content-Type-Options: nosniff`.
2. Сохранить require_project_role(viewer) до svc.get_photo, все три scoped IDs, body/MIME/Content-Disposition и LookupError404.
3. Проверки: правильные headers/bytes/MIME; denied403 не читает сервис; missing404 не превращается в200; foreign project не получает bytes. Не переписывать старые tests или новые receipt/size/actor guards.
4. Никаких migrations/storage/OAuth/serviceworker/global middleware, Clear-Site-Data storage/cookies, удаления IDB очередей или скачивания реальных фото. Новые headers не удалят старые HTTP cache entries и не запретят сохранённые копии/screenshots.
5. После реального модельного diff: exact source/head и minimal-hunk review; узкие synthetic tests, затем доступный HTTP/browser cache сценарий. Новые headers пока UNRUN/OPEN; ни draft PR, ни успешная отправка не являются приёмкой.

## Продолжение

Задание реализации ожидает допустимого исполнительного канала/реального diff; текущий review завершён. Независимый следующий P0 по NEXT — две числовые ошибки stage1-finite; при scheduled curator-only роли выполнять review/постановку и доступные проверки, не заменять DeepSeek кодом. Production/боты/службы/секреты не затронуты. Основной candidate и старый проверенный ZIP00306095 неизменны. Эта документация хранится отдельной review-веткой `refs/heads/review/stage3-photo-cache-20261105`.
