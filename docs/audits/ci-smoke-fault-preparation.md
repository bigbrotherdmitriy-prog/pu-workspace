# Подготовка аварийного Docker smoke

Дата: 2026-09-03. Ветка: `codex/ci-smoke-integration`.
База работы: `0289a9999056649e6cc1955003775babdf3f7165`
(локальный отчёт поверх проверенного на GitHub `1ac10e2…`).

## Изменения

- В существующий workflow добавлен opt-in fault-прогон по метке
  `[ci-smoke-fault]` в HEAD commit message только при push тестовой ветки.
- Повторный bootstrap после успешного API smoke сохраняет фактический
  exit code. Нет continue-on-error, подавления сбоя или изменения Core.
- Отдельный always-шаг подтверждает точную ожидаемую причину HTTP 409.
- Диагностика содержит machine-readable fault assertion.
- После cleanup проверяются остатки только конкретного Compose project
  и отсутствие временных секретов/raw-log файлов.
- Cleanup report загружается отдельным artifact и для обычных запусков.

## Локальные проверки

`python -m pytest scripts/ci/tests -q -p no:cacheprovider`:
**70 passed, 2.80 s**.

Тесты включают реальные Bash-блоки с mock Docker и исполнение встроенного
Python: сохранение exit 0/1/17, точный bootstrap 409, отклонение 403 и
таймаута, остатки volume, ошибки daemon, сохранённый временный env-файл,
точные label-фильтры всех трёх видов ресурсов и безопасные отчёты.

`actionlint -shellcheck= -pyflakes= .github/workflows/docker-smoke.yml .github/workflows/ci.yml`:
PASS, actionlint 1.7.12. ShellCheck/Pyflakes отдельно не запускались.
`git diff --check`: PASS.

Backend/frontend не менялись и повторно локально не запускались:
предыдущий реальный GitHub CI подтвердил 382 backend и 17 frontend тестов.

## Что ещё требуется

Этот коммит подготовлен локально. Новый push требует разрешения пользователя.
После push ожидается красный smoke job при успешных шагах проверки причины,
diagnostics/artifact и cleanup-verification. Сам по себе красный статус
не доказывает прохождение fault-теста.

Нужно получить оба artifacts, проверить HTTP 409 и нулевые остатки ресурсов,
отдельно зафиксировать остальные ошибки, если они появятся. До этого полный
протокол остаётся **CONDITIONAL**. Обычный smoke ранее подтверждён на GitHub.

Подробные критерии: [инструкция](../ci/docker-compose-smoke.md#намеренный-сбой-и-проверка-cleanup).
Основная грязная worktree, параллельные задачи, production, локальные
Docker/WSL не изменялись. Merge и deploy не выполнялись.
