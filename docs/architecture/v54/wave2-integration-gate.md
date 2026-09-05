# V5.4 Wave 2 integration gate

## Назначение

`scripts/ci/v54_wave2_gate.py` — read-only структурный предохранитель перед
интеграцией четырёх независимых потоков:

- mailbox identity/cutover;
- encrypted staging;
- evidence fragment reader;
- evidence UI.

Он читает только Git objects, ничего не checkout/cherry-pick/merge, не запускает
код кандидатов и не подключается к БД, провайдерам или production. Успешный
результат означает только прохождение структурных правил этой волны. Он не
заменяет code review, pytest, проверку миграций на PostgreSQL и browser E2E.

## Запуск

Из корня репозитория:

```text
python scripts/ci/v54_wave2_gate.py \
  --base-sha <exact-full-base-sha> \
  --mailbox-sha <exact-full-mailbox-sha> \
  --staging-sha <exact-full-staging-sha> \
  --evidence-sha <exact-full-evidence-sha> \
  --ui-sha <exact-full-ui-sha> \
  --output artifacts/v54-wave2-gate.json
```

Каждый SHA должен быть полным 40- или 64-символьным object ID, существовать
локально, обозначать commit, отличаться от base и иметь exact base своим
предком. Exit code: `0` — все проверки пройдены; `1` — структурный отказ;
`2` — безопасный JSON-протокол не удалось записать.

Git вызывается только списком аргументов с `shell=False`, закрытым stdin,
таймаутом и лимитами объёма. Скрипт не читает `.env`; путь результата задаёт
оператор.

## Контракт владения файлами

| Поток | Разрешённая область |
|---|---|
| Mailbox | только зафиксированный ниже exact write-set, включая `schema.py` и migrations `a54f001c0a03`/`a54f001c0a04` |
| Staging | `backend/app/staging/**` и профильные backend tests |
| Evidence | только `backend/app/source_evidence/fragment_reader.py` и `backend/tests/test_v54_fragment_reader.py` |
| UI | только `frontend/src/modules/evidence/**` |

Mailbox write-set не расширяется по совпадению слов `mail`, `gmail` или
`identity` в имени. Разрешены ровно:

```text
backend/app/api/ai_secretary.py
backend/app/api/gmail.py
backend/app/api/google_drive.py
backend/app/core/v54_authority.py
backend/app/core/v54_refs.py
backend/app/integrations/google_workspace.py
backend/app/mailbox_identity/__init__.py
backend/app/mailbox_identity/dto.py
backend/app/mailbox_identity/oauth.py
backend/app/mailbox_identity/runtime.py
backend/app/mailbox_identity/service.py
backend/app/models/__init__.py
backend/app/models/ai_secretary.py
backend/app/models/mailbox_identity.py
backend/app/schema.py
backend/migrations/versions/a54f001c0a03_v54_mailbox_identity_expand.py
backend/migrations/versions/a54f001c0a04_v54_mailbox_dedup_cutover.py
backend/tests/test_v54_mailbox_identity.py
backend/tests/test_v54_pilot_foundation.py
docs/architecture/v54/mailbox-cutover/README.md
docs/audits/v54-mailbox-cutover.md
docs/audits/v54-mailbox-identity-implementation.md
scripts/audits/v54_mailbox_inventory.py
```

Mailbox сохраняет существующий base revision `a54f001c0a02` и обязан дать одну
линейную цепочку:

```text
a54f001c0a02 -> a54f001c0a03 -> a54f001c0a04
```

Остальные потоки не могут менять миграции или `schema.py`. Для всех потоков
запрещены `App.tsx`, `backend/app/jobs/**`, production Compose и имена файлов,
похожие на secrets/keys/credentials.

## Проверяемые защитные свойства

Валидатор анализирует список изменений, добавленные строки и Python AST и
отказывает при следующих признаках:

- второй `BackgroundJob`, queue, ledger или source registry;
- AUTO/external action/enabled flag, включённый по умолчанию;
- body/content/attachment bytes/base64/token/password/DSN/filesystem path в
  job payload;
- попытка читать `.env`, production database URL или credentials;
- filename, URL, owner/project, plaintext metadata или path в staging
  descriptor;
- изменение evidence facade вместо добавления reader;
- fetch/endpoints/localStorage/dangerous HTML/mutation controls в UI;
- несинтетические email/OAuth/provider ID и встроенный PDF/base64 document
  material в новых tests;
- безусловные `skip`/`xfail` и условные skip вне явно проверяемых
  platform/PostgreSQL условий;
- ошибки `git diff --check`.

Activation detector применяется только к добавленным production source-файлам,
а не к migrations, docs или tests; имена вроде `autoincrement` не считаются
флагом. UI behavior scanner также проверяет production `.ts/.tsx`, отделяя их
от negative assertions в `.test.*`/`.spec.*`.

Условный `pytest.skip` разрешён только для явно обнаруживаемой недоступности
platform capability (например symlink/hardlink) либо отдельного PostgreSQL test
environment. Такой skip отмечается ограничением
`conditional_platform_or_postgres_tests_may_skip`: структурный gate может пройти,
но это не означает runtime PASS. Безусловные skip/xfail остаются отказом.

Это консервативный gate: совпадение с запретным структурным шаблоном требует
ручной проверки и исправления, а не обхода правила.

## Безопасный протокол

JSON содержит только:

- версию схемы протокола;
- итог `pass`/`fail`;
- exact base SHA;
- количество пройденных/проваленных проверок;
- стабильные ID, коды и имя потока для каждой проверки;
- статический список ограничений.

В него намеренно не попадают changed paths, diff body, исходники, Git stderr,
response bodies, найденные значения и содержимое потенциальных secrets. При
неожиданной ошибке публикуется только нейтральный `fatal_code`.

## Ограничения и обязательные последующие проверки

- Эвристики не доказывают семантическую корректность реализации и не являются
  полноценным secret scanner/SBOM scanner.
- AST-проверка работает для Python payload/descriptor; динамически собранные
  значения требуют code review и специализированных tests.
- Валидация цепочки подтверждает revision/down_revision и ожидаемый
  `CURRENT_SCHEMA_REVISION`, но не выполняет `alembic upgrade`.
- Скрипт не запускает кандидатов, PostgreSQL, workers, API или браузер.
- Разрешённые platform/PostgreSQL conditional skips не доказывают выполнение
  соответствующего runtime сценария.
- После PASS интегратор всё равно запускает тесты каждого потока, миграции на
  чистом PostgreSQL, общий regression suite и browser E2E.
