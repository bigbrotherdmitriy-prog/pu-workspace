# Management/Governance Audit-History — Two Architectures Compared

Дата: 2026-09-11

Статус: **чтение и документирование, не решение.** По прямому указанию
пользователя — зафиксировать обе архитектуры подробно, с плюсами и
минусами, **без выбора и без реализации**, для рассмотрения при разработке
MVP-3. Продолжение п. 2.1 [`mvp1-main-integration-alembic-preflight.md`](mvp1-main-integration-alembic-preflight.md)
(коллизия 1 — `record_version`/append-only history на `obligations`/`risks`/
`decisions`/`meetings`), которая решением по Alembic-области **не
переносится** ни в каком виде.

Обе архитектуры — не гипотетические: каждая реально написана, замаплена на
модели и **активно используется** на своей ветке (main — в проде через API,
review — через отдельный модуль `app/mvp3/lifecycle.py`). Ниже — точное
содержимое обеих, без упрощений.

## 1. Design A — main: единая полиморфная `management_history`

### 1.1. Схема (миграция `c93b7f4a21d0`, уже применена на main)

```sql
CREATE TABLE management_history (
    id               SERIAL PRIMARY KEY,
    organization_id  INTEGER NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity_type      VARCHAR(50) NOT NULL,
    entity_id        INTEGER NOT NULL,
    record_version   INTEGER NOT NULL,
    action           VARCHAR(50) NOT NULL,
    actor_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    old_values       JSON NOT NULL,
    new_values       JSON NOT NULL,
    evidence         JSON,
    reason           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (record_version > 0)
);
-- индексы: organization_id, project_id, entity_type, entity_id, actor_user_id, created_at
```

Плюс отдельная простая колонка `record_version` (optimistic-concurrency
счётчик) добавлена напрямую на сами версионируемые таблицы:
`obligations`, `meetings`, `notifications`, `risks`, `decisions`,
`project_contacts`, `tasks` — единым списком (`VERSIONED`-кортеж в
миграции), одним и тем же паттерном для всех.

### 1.2. ORM-модель (`app/models/management.py`)

```python
class ManagementHistory(Base):
    """Append-only audit trail for human-visible MVP3 state transitions."""
    __tablename__ = "management_history"
    id: Mapped[int]
    organization_id: Mapped[int]
    project_id: Mapped[int]
    entity_type: Mapped[str]
    entity_id: Mapped[int]
    record_version: Mapped[int]
    action: Mapped[str]
    actor_user_id: Mapped[int]
    old_values: Mapped[dict]
    new_values: Mapped[dict]
    evidence: Mapped[dict | list | None]
    reason: Mapped[str | None]
    created_at: Mapped[datetime]
```

**Никакого `event.listen`-иммутабельность-guard'а нет.** Append-only —
только докстринг-конвенция и то, что ни один вызывающий код на main её не
обновляет/удаляет (проверено — единственный писатель ниже). Ничто на
уровне ORM/БД не мешает случайному `UPDATE`/`DELETE`.

### 1.3. Как реально используется (`app/api/management.py`)

Один центральный хелпер, вызываемый из мутирующих эндпоинтов:

```python
def append_management_history(db, *, project_id, entity_type, entity_id,
                                record_version, action, actor_user_id,
                                old_values, new_values, evidence=None, reason=None):
    db.add(ManagementHistory(
        organization_id=_project_organization_id(db, project_id), project_id=project_id,
        entity_type=entity_type, entity_id=entity_id, record_version=record_version,
        action=action, actor_user_id=actor_user_id,
        old_values=jsonable_encoder(old_values), new_values=jsonable_encoder(new_values),
        evidence=jsonable_encoder(evidence) if evidence is not None else None, reason=reason,
    ))
```

Вызывается сейчас как минимум из `update_obligation`/`finish_meeting` (через
`_locked_versioned`-обвязку с optimistic-lock проверкой `expected_record_version`).
Есть read-эндпоинт `management_history(entity_type, entity_id, project_id, ...)`
— общий, работает для любого `entity_type` без изменений кода.

### 1.4. Семантика записи

**Diff-снимок состояния "было/стало".** Каждая запись — это `old_values`/
`new_values` (полные JSON-дампы затронутых полей до и после), плюс
свободный `action` (строка-ярлык действия) и опциональный `evidence`.
Не моделирует явный конечный автомат состояний (`from_status`/`to_status`)
— это можно восстановить только парсингом `old_values`/`new_values`
приложением, если статус был среди изменённых полей.

## 2. Design B — review: раздельные `obligation_history` + `governance_history`

### 2.1. Схема (миграция `a54f001c0a10`, не применена на main)

```sql
-- Расширяет obligations множеством новых колонок сверх main:
ALTER TABLE obligations ADD COLUMN due_time TIME;
ALTER TABLE obligations ADD COLUMN timezone VARCHAR(100) DEFAULT 'Europe/Moscow' NOT NULL;
ALTER TABLE obligations ADD COLUMN deadline_policy JSON;
ALTER TABLE obligations ADD COLUMN escalation_level INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE obligations ADD COLUMN last_escalated_at TIMESTAMPTZ;
ALTER TABLE obligations ADD COLUMN evidence_pins JSON;
ALTER TABLE obligations ADD COLUMN review_state VARCHAR(30) DEFAULT 'unverified' NOT NULL
    CHECK (review_state IN ('unverified','needs_review','verified'));

-- risks/decisions получают прямые FK на obligations/tasks + review_state/evidence_pins:
ALTER TABLE risks ADD COLUMN obligation_id INTEGER REFERENCES obligations(id) ON DELETE SET NULL;
ALTER TABLE risks ADD COLUMN task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL;
ALTER TABLE risks ADD COLUMN evidence_pins JSON;
ALTER TABLE risks ADD COLUMN review_state VARCHAR(30) DEFAULT 'unverified' NOT NULL CHECK (...);
-- то же самое для decisions, плюс decisions.owner_user_id, decisions.risk_id

CREATE TABLE obligation_history (
    id                  SERIAL PRIMARY KEY,
    obligation_id       INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
    project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sequence            INTEGER NOT NULL,
    event               VARCHAR(40) NOT NULL,
    from_status         VARCHAR(40),
    to_status           VARCHAR(40) NOT NULL,
    resulting_version   INTEGER NOT NULL,
    actor_user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reason              TEXT,
    evidence_pins       JSON,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (obligation_id, sequence)
);

CREATE TABLE governance_history (
    id                  SERIAL PRIMARY KEY,
    project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity_type         VARCHAR(20) NOT NULL,   -- 'risk' | 'decision'
    entity_id           INTEGER NOT NULL,
    sequence            INTEGER NOT NULL,
    event               VARCHAR(40) NOT NULL,
    from_status         VARCHAR(50),
    to_status            VARCHAR(50) NOT NULL,
    resulting_version   INTEGER NOT NULL,
    actor_user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reason              TEXT,
    evidence_pins       JSON,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, sequence)
);
```

### 2.2. ORM-модели (`app/models/management.py`, `app/models/governance.py`)

```python
class ObligationHistory(Base):
    __tablename__ = "obligation_history"
    obligation_id, project_id, sequence, event, from_status, to_status,
    resulting_version, actor_user_id, reason, evidence_pins, occurred_at

def _deny_obligation_history_mutation(mapper, connection, target):
    raise ValueError("management_history_is_append_only")
event.listen(ObligationHistory, "before_update", _deny_obligation_history_mutation)
event.listen(ObligationHistory, "before_delete", _deny_obligation_history_mutation)


class GovernanceHistory(Base):
    __tablename__ = "governance_history"
    project_id, entity_type, entity_id, sequence, event, from_status, to_status,
    resulting_version, actor_user_id, reason, evidence_pins, occurred_at

def _deny_governance_history_mutation(mapper, connection, target):
    raise ValueError("management_history_is_append_only")
event.listen(GovernanceHistory, "before_update", _deny_governance_history_mutation)
event.listen(GovernanceHistory, "before_delete", _deny_governance_history_mutation)
```

**ORM-level immutability enforcement есть** — `before_update`/`before_delete`
listener'ы явно кидают исключение при попытке изменить/удалить строку
истории через SQLAlchemy (не защищает от прямого SQL в обход ORM, но
защищает от случайной мутации внутри приложения).

### 2.3. Как реально используется (`app/mvp3/lifecycle.py`, review-only модуль)

```python
db.add(ObligationHistory(obligation_id=row.id, project_id=scope.project_id, sequence=1, ...))
...
db.add(ObligationHistory(obligation_id=row.id, project_id=scope.project_id, sequence=seq, ...))
...
db.add(GovernanceHistory(project_id=row.project_id, entity_type=entity_type, entity_id=row.id, ...))
```

Отдельный, целиком новый модуль `app/mvp3/lifecycle.py`, не существующий
на main вообще — управляет sequence-нумерацией (`sequence=1` для первой
записи, инкремент далее) и явными `from_status`/`to_status` переходами.

### 2.4. Семантика записи

**State-machine транзиция.** Каждая запись — `event` (тип перехода,
например `"created"`/`"escalated"`/`"confirmed"`) + явные `from_status`/
`to_status`, строго упорядоченные `sequence`-номером per-entity (не
per-таблица, а именно per-`obligation_id`/`(entity_type, entity_id)`).
Не хранит полный before/after снимок полей — только статус-переход
(плюс опциональные `reason`/`evidence_pins`). Если нужно узнать "что именно
изменилось", кроме статуса — эта таблица не отвечает; нужно смотреть
текущее состояние сущности или комбинировать с чем-то ещё.

## 3. Сопоставление напрямую, по критериям

| Критерий | Design A (main, `management_history`) | Design B (review, `obligation_history`+`governance_history`) |
|---|---|---|
| Число таблиц | 1 (полиморфная, `entity_type`/`entity_id`) | 2 (`obligation_history` — FK-типизирована на `obligations`; `governance_history` — полиморфная на `risks`/`decisions`) |
| Что фиксируется | Полный snapshot diff (`old_values`/`new_values` JSON) + свободный `action`-ярлык | Явный конечный автомат (`from_status`→`to_status`) + `event`-тип, без diff'а прочих полей |
| Упорядочивание записей | Только `created_at` (нет per-entity sequence) | Явный `sequence` (per `obligation_id` / per `(entity_type, entity_id)`), с `UNIQUE`-constraint — гарантированно без пропусков/дублей по построению |
| Immutability enforcement | Нет (только конвенция/докстринг) | Есть, `before_update`/`before_delete` event listener'ы на ORM-уровне |
| Организационный охват | `organization_id` явно хранится в каждой записи (мультитенантный audit сразу без join'а) | Нет `organization_id` — нужен join через `project`/`obligation` для мультитенантных отчётов |
| Расширяемость на новый entity_type | Тривиально — просто новое значение `entity_type`, без миграции схемы | Требует либо новой таблицы (как `obligation_history` для одного типа), либо добавления в существующий полиморфный `governance_history` — то есть уже сегодня 2 разных паттерна одновременно (типизированный для obligations, полиморфный для risks/decisions) — непоследовательно само по себе |
| `evidence` | Одно поле `evidence` (JSON, свободная форма) | `evidence_pins` (JSON) — то же по факту, другое имя |
| Связанность с scheduling/эскалацией | Не имеет отношения — история отделена от самой сущности; main's `obligations` не имеет `due_time`/`timezone`/`deadline_policy`/`escalation_level`/`last_escalated_at`/`review_state` вообще | Design B неотделим от параллельного, гораздо более богатого расширения самой таблицы `obligations` (due_time/timezone/deadline_policy/escalation_level/evidence_pins/review_state) — историю нельзя перенести без этого контекста, если цель — воспроизвести semantics review's эскалационной модели |
| Готовность/статус на своей ветке | В проде, используется как минимум `update_obligation`/`finish_meeting`, есть generic read-эндпоинт `management_history(entity_type, entity_id, project_id)` | Используется в `app/mvp3/lifecycle.py` (review-only, целый модуль, включая логику эскалации и review-workflow, которой на main нет вовсе) |
| Стоимость "просто перенести" | — (уже на main) | Не тривиальна: как показано в Alembic preflight п. 2.1, миграция коллизирует с уже примененными на main колонками (`obligations`/`risks`/`decisions`/`meetings`.`record_version`) — перенос требует либо replace, либо reconciliation, не аддитивного добавления |
| Query-паттерн "история одной obligation по порядку" | `SELECT ... WHERE entity_type='obligation' AND entity_id=X ORDER BY created_at` — работает, но без гарантии, что `created_at` строго монотонна при конкурентных записях (нет unique constraint на порядок) | `SELECT ... WHERE obligation_id=X ORDER BY sequence` — порядок гарантирован уникальным constraint'ом на `(obligation_id, sequence)`, конкурентная запись с одинаковым `sequence` физически отвергается на уровне БД |
| Query-паттерн "вся история проекта одним запросом, все типы" | Один запрос по `project_id` без `UNION` — `entity_type` уже в той же таблице | Требует `UNION`/два запроса (`obligation_history` + `governance_history` раздельно) для сводной картины по проекту |

## 4. Плюсы и минусы каждой — прямо

### Design A (main, `management_history`)

**Плюсы:**
- Уже в проде, уже написан, уже подключён к реальным эндпоинтам —
  нулевая стоимость "начать использовать".
- Один запрос покрывает всю историю проекта по всем типам сразу (нет
  `UNION`).
- `organization_id` на записи — сразу пригоден для мультитенантной
  отчётности/аудита без join через project.
- Расширяется на новый `entity_type` без единой миграции схемы.
- Полный before/after snapshot — можно восстановить "что именно
  изменилось" по ЛЮБОМУ полю, не только статусу, без заранее заданного
  списка отслеживаемых переходов.

**Минусы:**
- Нет ORM-level защиты от `UPDATE`/`DELETE` — append-only только по
  конвенции.
- Нет строгого per-entity порядка (`sequence`) — при конкурентной записи
  возможна неоднозначность порядка внутри одной секунды `created_at`
  (не проверено эмпирически, но конструктивно не исключено constraint'ом).
- `old_values`/`new_values` как JSON-диффы: чтобы понять "произошёл ли
  именно переход статуса", нужно доставать конкретные ключи из JSON в
  коде читателя — нет декларативной `to_status`-колонки для прямой
  фильтрации/индексации по статусу-результату.
- Не имеет отношения к богатой obligations-специфичной модели
  (escalation/due_time/timezone/deadline_policy) — если она понадобится,
  придётся проектировать заново, `management_history` тут ничем не
  поможет и не мешает.

### Design B (review, `obligation_history` + `governance_history`)

**Плюсы:**
- Строгий, БД-гарантированный порядок (`sequence` + `UNIQUE`) — надёжнее
  для "точной истории событий одной сущности", чем `created_at`.
- ORM-level immutability enforcement — случайный `db.commit()` после
  `.update()`/`.delete()` объекта истории физически упадёт с исключением,
  а не тихо пройдёт.
- Явные `from_status`/`to_status` — прямая пригодность для
  индексации/фильтрации "все переходы В статус X" без парсинга JSON.
- Идёт в комплекте с уже спроектированной, значительно более богатой
  obligations-моделью (эскалация, дедлайны с таймзоной, evidence-pinning,
  review-workflow) — не изолированный кусок, а часть цельного,
  продуманного MVP-3-lifecycle дизайна (`app/mvp3/lifecycle.py`).
- Прямые FK risks/decisions → obligations/tasks — облегчает join-запросы
  "все риски, связанные с этим обязательством" без промежуточного слоя.

**Минусы:**
- Две таблицы с двумя РАЗНЫМИ паттернами полиморфизма одновременно
  (typed `obligation_history` для одного типа vs polymorphic
  `governance_history` для двух других) — сама архитектура не полностью
  единообразна; для третьего будущего типа сущности неочевидно, к какому
  из двух паттернов его отнести.
- Сводная история по всему проекту требует `UNION` двух таблиц (плюс
  потенциально третьей/четвёртой, если появятся другие типизированные
  history-таблицы по тому же образцу, что `obligation_history`).
- Нет `organization_id` на записи — мультитенантные отчёты требуют join.
- Не хранит generic before/after diff — если понадобится узнать "что
  ещё, кроме статуса, изменилось" (например, поменялся `title` без смены
  `status`), эта таблица такое событие вообще не увидит (нет записи без
  смены `to_status`, судя по schema — `event`/`to_status` обязательны).
- Перенос на main не аддитивен — коллизирует с уже применённым
  `record_version` на тех же таблицах (Alembic preflight п. 2.1);
  необходимо reconciliation, не порт.
- Тянет за собой (или, наоборот, предполагает) значительно больший объём
  сопутствующего кода/схемы (`due_time`/`timezone`/`deadline_policy`/
  `escalation_level`/`evidence_pins`/`review_state` на `obligations` и
  аналогичные на `risks`/`decisions`) — не может быть оценён как
  изолированное решение "просто другая история", это часть более
  крупного product-решения об эскалации/review-workflow, которого на
  main нет вообще ни в каком виде.

## 5. Что это НЕ говорит

Это не рекомендация. Ни одна колонка/таблица здесь не помечена
"выбрать" — обе архитектуры рабочие, обе используются на своей ветке,
у обеих разные компромиссы (диапазон охвата истории vs строгость порядка;
нулевая стоимость сегодня vs согласованность с более богатой моделью
эскалации). Решение — за MVP-3 разработкой, когда придёт время
проектировать её lifecycle-требования по существу, а не как побочный
эффект Alembic-порта.
