# MVP3 digest preferences and proposal recovery

Дата: 2026-09-05

Ветка: `codex/mvp3-digest-preferences`

База: `6ceb635190e578fdbc596f07b00a25feb8be0122`

## Краткий аудит до изменений

1. `MeetingDigestService` уже создавал только внутренний агрегированный
   `Notification`, учитывал IANA timezone и quiet-hours и не вызывал внешний
   provider.
2. `mvp3.management_digest` уже исполнялся существующим `BackgroundJob`, но
   preference целиком передавался клиентом в ручной POST и не сохранялся.
3. Общий scheduler запускал Gmail и AI rules, но не выполнял проход по
   пользовательским настройкам digest.
4. Связь meeting/message с созданным Obligation/Decision оставалась только в
   строке `AuditLog.details`. После перезагрузки безопасно восстановить список
   предложений было невозможно.
5. Подтверждение повторно не проверяло доступность и актуальность exact
   Evidence pin непосредственно перед CAS.

## Реализовано

### Сохранённые preference

- Новая scoped-модель `ManagementDigestPreference`, уникальная для
  `project_id + user_id`.
- Поля: IANA `timezone`, `quiet_start`, `quiet_end`, `channel` (`in_app` или
  `disabled`), `cadence` (`daily` или `weekdays`) и `record_version`.
- GET возвращает безопасный, но несохранённый default: `in_app`, daily,
  `Europe/Moscow`, quiet-hours 20:00–08:00, `record_version=0`.
- Unsaved default сам по себе не создаёт background job. Планирование
  начинается только после явного PUT.
- PUT работает только для текущего пользователя в доступном проекте и требует
  CAS: `expected_version=0` при создании, текущая версия при обновлении.
- Audit preference содержит только project/user ID и version, без timezone,
  quiet-hours и пользовательского содержимого.

API:

- `GET /management/v2/projects/{project_id}/digest-preference`;
- `PUT /management/v2/projects/{project_id}/digest-preference`.

### Durable cadence

- Новый проход `schedule_digest_jobs()` вызывается из уже существующего
  `app.jobs.scheduler.schedule_once()`; второй scheduler и вторая очередь не
  создавались.
- Disabled preference, weekend для `weekdays` и активные quiet-hours не
  создают job.
- Вне quiet-hours создаётся один idempotent job на preference version и
  локальную дату.
- Новый payload содержит только `project_id`, `user_id`, `local_date`,
  `preference_id`, `preference_version`.
- Worker повторно читает preference из БД. Изменённая/удалённая/чужая версия
  даёт безопасный `stale_preference`; конфигурация из payload не принимается.
- Legacy явный POST digest оставлен совместимым. Внешние каналы не добавлены.

### Восстановление proposed actions

- Новая append-only модель `ManagementProposalOrigin` связывает
  meeting/message с target entity, исходным proposal kind и exact Evidence
  pins.
- Повторный POST идемпотентно использует ту же связь; несовместимый повтор
  закрывается отказом.
- Добавлены read-only endpoints:
  - `GET /management/v2/meetings/{meeting_id}/proposals?project_id=...`;
  - `GET /management/v2/messages/{message_id}/proposals?project_id=...`.
- На каждом GET повторно проверяются tenant/project, origin, актуальный
  `SourceCurrent`, availability/freshness/assessment, exact pin и совпадение
  evidence с текущим message SourceReference.
- Ответ явно содержит `manual_review_required`. Никаких Task/provider effects
  read endpoint не создаёт.
- Непосредственно перед manager confirmation exact Evidence проверяется снова;
  stale/revoked evidence не может быть подтверждён.

## Schema-owner request

Миграция намеренно не создавалась по границам параллельного потока. Перед
активацией кода schema owner должен создать интеграционную ревизию
`a54f001c0a17` от актуальной единственной head (ожидается
`a54f001c0a16`; это необходимо подтвердить после переноса соседнего потока).
На изолированной базе этой ветки текущая head остаётся `a54f001c0a15`.

Нужно создать:

1. `management_digest_preferences`:
   - PK `id`;
   - FK `project_id -> projects.id ON DELETE CASCADE`;
   - FK `user_id -> users.id ON DELETE CASCADE`;
   - `timezone varchar(100)`, `quiet_start time`, `quiet_end time`;
   - `channel varchar(20)`, `cadence varchar(20)`, `record_version integer`;
   - `created_at`, `updated_at` timezone-aware;
   - unique `(project_id, user_id)`;
   - checks `record_version > 0`, `channel IN ('in_app','disabled')`,
     `cadence IN ('daily','weekdays')`;
   - индексы по `project_id`, `user_id`.
2. `management_proposal_origins`:
   - PK `id`, FK project/user;
   - `origin_type`, `origin_id`, `entity_type`, `entity_id`, `proposal_kind`;
   - JSON `evidence_pins`, `created_at`;
   - unique `(project_id, origin_type, origin_id, entity_type, entity_id)`;
   - checks для разрешённых origin/entity/proposal kinds;
   - индексы по project, origin и target IDs.

Исторические `AuditLog.details` нельзя считать надёжным relational source.
Автоматический backfill proposal origins запрещён; старые предложения следует
показывать без origin-связи до явного повторного evidence review.

## Проверки

- Новые/затронутые digest, proposal, API и scheduler tests: `35 passed`.
- Весь функциональный набор MVP3: `74 passed`.
- Полный backend: `1297 passed, 19 skipped`, две ошибки существующей базы:
  `test_mvp3_foundation_is_single_sequential_head` и
  `test_budget_dds_migration_is_single_sequential_head` ожидают, что текущая
  head `a54f001c0a15` имеет parent `a54f001c0a13`, хотя уже существующая
  миграция `a54f001c0a15_provider_product_outbox.py` корректно указывает
  `down_revision = a54f001c0a14`. Эти два stale assertions присутствуют в
  исходном commit `6ceb635` и данным потоком не изменялись.
- Python compileall: PASS.
- `git diff --check`: PASS.
- PostgreSQL concurrency не запускалась; SQLite не доказывает конкурентный
  create/CAS и unique conflict.

## Ограничения

- До schema migration новый код нельзя активировать в runtime.
- Каналы email/Telegram и любые внешние действия намеренно отсутствуют.
- `daily` означает один проход в первый scheduler tick вне quiet-hours на
  локальную дату; отдельное пользовательское время доставки не вводилось.
- Для meeting нет durable SourceReference самого протокола, поэтому GET может
  доказать точные project Evidence pins, но не семантическое происхождение
  каждого pin из аудиозаписи/протокола встречи. Message origin проверяется
  строго по SourceReference.
- Production, Google, Gmail, Telegram, Drive, finance/supply и `App.tsx` не
  изменялись. Push, merge и deploy не выполнялись.
