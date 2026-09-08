# V7 synthetic canary/readiness gate

Дата аудита: 2026-09-08. База: `4c218a4b5433b0e259c40acb1747cbc1270c4ae2`.

## Назначение

Gate даёт один воспроизводимый ответ на практический вопрос: готов ли текущий кандидат к ограниченному пользовательскому canary после отдельного деплоя. Он проверяет чистую PostgreSQL-миграцию и существующие продуктовые regression-пробы цепочки: привязка mock-хранилища → анализ → договор → WBS → ДДС → финансовый прогноз → задача → mock-email.

Все пробы синтетические. Google, Яндекс, Gmail, Telegram и внешний AI не вызываются. Любая низкая уверенность, финансовое действие или завершение задачи без человека должны оставаться заблокированными.

## Аудит до изменений

- Общий CI уже выполнял полный backend/frontend набор и миграции.
- Отдельные workflow проверяли очередь, snapshot recovery и storage runtime.
- WBS имел самостоятельный acceptance validator.
- Единого ежедневного пользовательского readiness-вердикта по всей цепочке не было.

## Контракт

- Запуск разрешён только с `CI_CANARY_SYNTHETIC_ONLY=true`.
- Scope имеет непрозрачный digest; исходное имя проекта в artifact не сохраняется.
- Разрешён только PostgreSQL на `localhost`, `127.0.0.1`, `postgres` или `db`, а имя БД обязано содержать `canary` или `test`.
- Наличие известных provider-token переменных немедленно запрещает запуск.
- Pytest stdout/stderr отбрасывается и не попадает в protocol или artifact.
- Artifact содержит только статусы, число проверок, длительности, exit code и хешированные receipts.
- Ошибка любой стадии даёт общий `FAIL`; частичный PASS не считается готовностью.

## Интерпретация

`PASS` означает: кандидат прошёл миграцию на чистой PostgreSQL и синтетические product probes минимальной рабочей цепочки. После этого всё равно требуются отдельный canary deploy, вход тестового пользователя и smoke с тестовыми подключениями.

`PASS` не доказывает: живые Google/Яндекс/Gmail API, реальные документы, production-конфигурацию, производительность, backup/restore production или отсутствие ошибок на конкретном пользовательском наборе данных.

До успешного workflow итоговый статус — `CONDITIONAL`.

## Локальная проверка

- Contract-тесты нового gate: `10 passed`.
- Полный синтетический canary: `PASS`, 8/8 стадий, 12 запрошенных product checks.
- Общий `scripts/ci`: `365 passed`; 6 существующих smoke-workflow тестов не исполнились из-за отсутствующего `/bin/bash` в локальном WSL, а не из-за canary gate.
- Python compilation и `git diff --check`: PASS.
- `actionlint`, Docker/Compose и чистая PostgreSQL локально недоступны. Их не заменяем статическими проверками; workflow остаётся обязательным.

## Запуск после отдельного разрешения

```bash
git push -u origin codex/v7-canary-readiness
gh workflow run v7-canary-readiness.yml --ref codex/v7-canary-readiness
```

Production, secrets, пользовательские данные и product core этим потоком не изменяются.
