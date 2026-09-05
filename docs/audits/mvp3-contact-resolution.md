# MVP3 M3-09 — Company / Person / Contact resolution

Дата: 2026-09-05

Ветка: `codex/mvp3-contact-resolution`
База: `65451cbb69177ddd0155d5f650ec6d64bae29ff8`

## Краткий аудит до изменения

Канонической записью контакта уже был `ProjectContact`, поэтому отдельный
identity registry не создавался. Найденные разрывы:

- email был уникален на всю организацию и не учитывал mailbox/project;
- отсутствовали нормализация домена и телефона;
- Gmail-discovery создавал неподтверждённую карточку, но решение человека
  выполнялось обычным `PATCH` без CAS и без истории;
- повтор команды и конфликт одинакового email в разных scope не имели
  формального контракта;
- audit не доказывал точную версию решения.

`Organization` в текущей модели является tenant/правообладателем проекта, а не
справочником контрагентов. Поэтому домен компании сохранён только как
объяснимый hint в существующей карточке, без ошибочного создания новой tenant
organization.

## Реализовано

- существующий `ProjectContact` расширен `mail_connection_id`,
  `normalized_domain`, `phone`, `normalized_phone`, `record_version`,
  `resolution_state`, `resolution_reason_code` и ссылкой на исходное сообщение;
- legacy email остаётся уникальным в пределах организации, а mailbox cohort
  допускает тот же email в другом mailbox/project;
- один и тот же sender в одном scope повторно использует одну proposal-запись;
- добавлен явный human endpoint `POST /project-contacts/{id}/resolve` с CAS;
- решения `confirm`, `correct`, `reject` имеют `decision_key`, безопасный replay
  и fail-closed collision;
- подтверждение через старый `PATCH` запрещено, чтобы нельзя было обойти CAS и
  историю;
- `ProjectContactHistory` append-only; сохраняет только имена изменённых полей,
  reason code и хеш snapshot/command, но не email/телефон;
- Gmail lookup/discovery использует точный `mail_connection_id` при активном
  mailbox cutover;
- UI подтверждения передаёт текущую версию и уникальный decision key;
- audit содержит project/version/reason и признак mailbox scope, без PII.

## Инварианты

1. Неподтверждённая карточка не маршрутизирует письмо автоматически.
2. Более одного подтверждённого кандидата в scope не выбирается «по первому».
3. Stale `record_version` возвращает 409.
4. Повтор идентичной команды возвращает `already_applied`; повтор ключа с иным
   содержимым возвращает 409.
5. Пользователь без project role не может подтвердить или исправить контакт.
6. История решения не редактируется и не удаляется ORM-операцией.
7. Контент письма, email и телефон не записываются в audit/history snapshot.

## Миграция

Локальная ветка имеет одну Alembic head `a54f001c0a12` с
`down_revision = a54f001c0a10`.

Параллельный поток ContractVersion резервирует `a54f001c0a11`. При интеграции
нужно содержательно перенести миграцию `a12` после `a11` и изменить только её
`down_revision` на `a54f001c0a11`. Merge migration и две головы не нужны.

## Проверки

- targeted backend: 44 PASS;
- full backend (isolated `--basetemp`): 1218 PASS, 19 PostgreSQL/environment skips;
- frontend Vitest: 102 PASS;
- frontend TypeScript check: PASS;
- frontend production build: PASS (generated `react_dist` не включён в commit);
- Alembic offline SQL: PASS;
- Alembic heads: одна `a54f001c0a12`;
- `git diff --check`: PASS.

Первый полный прогон использовал общую Windows temp-папку и получил 235
setup-error после её блокировки другим параллельным pytest-процессом. Повтор с
выделенной `--basetemp` прошёл полностью. PostgreSQL runtime не заявляется,
поскольку `TEST_POSTGRES_DSN` недоступен.

## Негативные сценарии

Покрыты: дубликат в одном scope, одинаковый email в разных mailbox/project,
конфликт двух проектов одного mailbox, stale CAS, replay, collision decision
key, отсутствие project role, попытка изменить history, PII в audit/history,
обход через legacy PATCH, конфликт contact/content из существующего Gmail
regression-набора.

## Ограничения и handoff

- live Gmail API и реальные клиентские данные не использовались;
- UI даёт подтверждение proposal; расширенная форма correction/reject пока
  доступна через API-контракт;
- phone не используется для автоматической маршрутизации: он хранится только
  после human correction и не является самостоятельным доказательством;
- для production acceptance нужен PostgreSQL upgrade `a10 -> a11 -> a12`,
  concurrent CAS двумя сессиями и mailbox-cohort smoke;
- production, DNS и production DB не изменялись; push/merge/deploy не выполнялись.
