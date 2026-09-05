# MVP3 management acceptance

Дата проверки: 2026-09-05

Ветка: `codex/mvp3-management-acceptance`

База: `9b9404a79ad336f68a4dc91b92f556cf7d512671`

## Решение

Synthetic/offline-часть `M3-11` — **PASS**. Полный `M3-11` —
**CONDITIONAL**, пока не выполнены PostgreSQL concurrency, browser E2E и
приёмка выбранного live-канала в отдельной изолированной среде.

Продуктовый дефект в проверенном offline-контуре не воспроизведён. Поэтому
product code не менялся: добавлены только acceptance-тесты и этот отчёт.

## Покрытый сквозной сценарий

| Участок | Доказательство |
| --- | --- |
| `M3-01` обязательство | exact Evidence pin, низкая уверенность, human review, CAS и append-only history |
| `M3-02` внутренняя задача | после manager confirm создаётся ровно одна Task; replay возвращает ту же Task; provider IDs отсутствуют |
| `M3-03` срок | timezone-aware просрочка попадает в attention как critical; quiet-hours и persisted preference покрыты существующим scheduler |
| `M3-04` риск/решение | evidence-backed Risk/Decision связаны с Obligation и Task; подтверждение сохраняет версии и history |
| `M3-05` встреча/сообщение | создаются только proposals; editor не подтверждает low-confidence proposal; stale Evidence закрывает чтение |
| `M3-06` attention | obligation, task, risk и decision видимы в одном explainable read model без внешних действий |
| `M3-07` digest | preference сохраняется с CAS; scheduler создаёт одно идемпотентное задание; worker создаёт одну in-app notification |
| `M3-08` договор | создание/редактирование/replay/stale CAS и immutable ContractVersion проверены вместе с поиском |
| `M3-09` контакт | mailbox-scoped proposal, human correction, replay, stale CAS и PII-minimized history/audit |
| `M3-10` поиск | фильтры project/contract/counterparty/date, permission scope, saved-view CAS и append-only history |

Дополнительно подтверждено:

- cross-tenant/cross-project доступ закрывается без раскрытия объекта;
- повтор предложения и повтор подтверждения не создают дубликаты;
- исправление ошибочного завершения обязательства требует причины и добавляет
  новую запись истории;
- `BackgroundJob.payload` digest содержит только `project_id`, `user_id`,
  `local_date`, `preference_id`, `preference_version`;
- payload не содержит письмо, протокол, документ, Evidence, email, excerpt,
  token или иной контент;
- `ProviderAction` не создаётся ни на одном шаге acceptance-сценария;
- тексты сообщения и email контакта не попадают в проверяемый audit trail.

## Добавленные тесты

- `backend/tests/test_mvp3_management_acceptance.py` — три сквозных SQLite
  synthetic/offline сценария;
- `backend/tests/test_mvp3_management_acceptance_postgres.py` — opt-in
  PostgreSQL CAS: две транзакции подтверждают одну версию обязательства,
  допускается ровно один победитель. Тест создаёт отдельную случайную schema и
  удаляет только её; обычная БД отвергается.

## Результаты

```text
Новые acceptance tests:                 3 passed, 1 skipped
M3-01..M3-10 + acceptance:             68 passed, 1 skipped
Management UI unit tests:              39 passed
Frontend TypeScript check:             PASS
git diff --check:                       PASS
```

Причина единственного skip: переменная
`PUW_MVP3_TEST_DATABASE_URL` с URL отдельной PostgreSQL БД не задана. Это не
считается runtime PASS.

## Команды обязательной внешней проверки

PostgreSQL URL должен указывать только на отдельную пустую базу, имя которой
начинается с `puw_mvp3_test_`; production URL тест отвергнет.

```powershell
$env:PUW_MVP3_TEST_DATABASE_URL = "postgresql+psycopg://<test-user>:<test-password>@127.0.0.1/puw_mvp3_test_acceptance"
Set-Location backend
python -m pytest -q tests/test_mvp3_management_acceptance_postgres.py
```

Browser E2E следует запускать после подъёма отдельного backend с синтетической
БД. Сам unit-проход не заменяет эту проверку:

```powershell
Set-Location frontend
npm run check:e2e
npm run test:e2e
```

Для live-channel acceptance необходим тестовый Google Workspace/Telegram
контур без пользовательских данных. Требуемая последовательность: создать
CONFIRM proposal, проверить отсутствие эффекта до approval, выполнить ровно
один подтверждённый effect, повторить request/restart/reconciliation и
проверить единственный receipt. Эта ветка live-вызовы не выполняет.

## Оставшиеся ограничения

1. PostgreSQL race/restart/replay не исполнены локально; подготовлен opt-in CAS
   тест, но он пока `CONDITIONAL`.
2. Browser E2E management center не исполнен. Пройдены только 39 unit-сценариев
   read model, panels и hook.
3. Live provider acceptance намеренно не выполнялся; synthetic acceptance
   доказывает только отсутствие неподтверждённого эффекта.
4. Attention повышает просрочку до `critical`, но автоматическое изменение
   сохранённого `Obligation.escalation_level/last_escalated_at` в этом потоке не
   реализовано и не объявляется готовым.
5. Digest проверен для `in_app`; внешние каналы остаются выключенными.

## Изменённые файлы

- `backend/tests/test_mvp3_management_acceptance.py`;
- `backend/tests/test_mvp3_management_acceptance_postgres.py`;
- `docs/audits/mvp3-management-acceptance.md`.

Production, миграции, schema pins, `App.tsx`, Gmail и provider action code не
изменялись. Push, merge и deploy не выполнялись.
