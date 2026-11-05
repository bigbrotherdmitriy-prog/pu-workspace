# NEXT — PU Workspace

## Сделано

P0 «выход без сети и отсутствие автоматического восстановления» завершён в изолированном проверенном объёме. Последний code commit **b894d194826a2bf25655b8e3d4b7067bc3413560**, `/tmp/pu-integration-git-20261103`, branch `work/post-handoff-safe-20261104`; после него — отдельный docs commit. Точный полный source HEAD и SHA256 брать из `docs/operations/OFFLINE-LOGOUT-CURRENT-TRANSFER-INPUTS-2026-11-05.json`, restore receipt рядом. При отсутствии этих файлов завершить упаковку текущего checkout, не использовать старый2f/ee83 пакет как актуальный.

- Local/server fence, session epochs, delayed-body guard, BFCache/multitab и пересоздание всего приватного React-состояния.
- Durable scoped revoke-only queue, повторы при сети/startup/online; ответ отзыва не стирает новую login-cookie.
- Предупреждение и отмена до выхода; сохранение actor-scoped unsent photo Blobs/правок/stock intents. Несохранённые поля формы сохранить заранее.
- 12code/verification commits до b894d194, явно Codex fallback; не результат DeepSeek. Прибавлен завершающий коммит HANDOFF/NEXT/NOTES/DECISIONS. Никаких push/merge/deploy.
- Проверки после исправлений: **877 frontend unit**, **100E2E**, **4227Python**, **132conditionalSKIP**,5subchecks. Матрицы учитывают целевые повторы; исходные failed full отчёты сохранены. 7testfiles sales-bot/dispatcher исключены по прямому запрету. Main/E2E TypeScript и builds0. Actual Chromium→HTTP→FastAPI/memorySQLite logout4PASS, свои процессы остановлены.
- Исходные root estimates diff и untracked сохранены, migration revision bytes не изменены. Production/liveDB/.env/секреты/службы/боты не трогали.

## В работе

Активной реализации следующего P0 в этом bounded задании нет. Источник истины: HANDOFF§6, матрицы в docs/operations, WORK-STATUS и .pu-autonomy/evidence. Stage3 целиком не объявлен done: реальный телефон и другие программные пункты остаются.

## Следующий шаг — P0 stage1-finite

1. Прочитать `docs/STAGE1-NUMERIC-REVIEW-GATES-2026-11-04.md` и два RED report: `STAGE1-ROUNDING-TIE-RED-2026-11-04.json`, `STAGE1-MANUAL-ROUNDTRIP-RED-2026-11-04.json`.
2. Закрыть tie-rounding и ручной round-trip без молчаливой перезаписи исторического total: `backend/app/estimates.py`, `backend/app/estimate_import.py`, соответствующие `backend/tests/test_estimates.py` / `test_estimate_import.py`. Сначала проверить актуальный diff/source head и результаты DS39–44; queued не равно модельному результату. При подтверждённо недоступном исполнении — явный Codex fallback.
3. Изолированные synthetic regressions и подходящие HTTP проверки; никаких production DB/реальных смет. Не применять автоматически два прежних root estimates diff.
4. Следующие независимые P1: no-store для authenticated defect photos; RD PDF/изображения viewer/сравнение. Не объявлять всё оставшееся внешними блокерами.

## Допущения и границы

NOTES.md: HttpOnly и общий HTTP disk cache физически недоступны JS офлайн; local fence/no-store/owned cache cleanup и server revoke предотвращают восстановление. Старый destructive logout default заменён прямым требованием сохранять unsent. Предварительные остальные решения — DECISIONS.md. Default PostgreSQL/opt-in skips не являются runtime приёмкой; realphone/camera/OS PWA/печать, реальные формы/смета/1С/Диадок отдельно.

## Владение и запреты

Перед мутациями git status/control/user pause и единственный work-owner lock/lease. Не обходить чужой lock; PID namespace не kill. Control обновлять атомарно под supervisor.lock, сохранять другие tasks и pause. Не clean/reset/untrackeddelete. No production/liveDB/deploy/approval/.env/secrets/sudo/services/bots/proxy/denied обход. DeepSeek основной; Codex fallback по исключению при недоступном исполнении с честным provenance. Новые обращения только .pu-autonomy, не старый alias. Общий MASTER-PLAN не завершён.
