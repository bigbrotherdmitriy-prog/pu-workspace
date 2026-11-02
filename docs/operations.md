# PU Workspace — эксплуатационный регламент

## Безопасное развёртывание

1. Развёртывать только зафиксированный коммит рабочей ветки; `main` напрямую не изменять.
2. До миграций создать PostgreSQL backup в custom format и проверить, что файл не пуст.
3. Собрать candidate image и выполнить весь набор тестов.
4. Сохранить image id действующего backend отдельным rollback-тегом.
5. Переключить release symlink, обязательно подключая постоянный proxy override.
6. После запуска проверить `/api/readiness`, `/api/status`, ревизию Alembic, Telegram и Gemini.

Обязательная compose-команда production:

```sh
docker compose -p app -f docker-compose.yml -f /opt/pu-workspace/docker-compose.proxy.yml
```

## Очереди и dead-letter

`GET /projects/{project_id}/processing-queue` показывает активные, ошибочные и исчерпавшие повторы операции. Ошибочная операция допускает два ручных повтора, то есть не более трёх попыток вместе с исходной. После этого она получает статус `dead_letter` и требует диагностики администратора.

Повтор построения снимка: `POST /projects/{project_id}/snapshots/{snapshot_id}/retry-build`.

Повтор organizer-сессии: `POST /organizer/sessions/{session_id}/retry`.

## Восстановление

1. Остановить backend и relay, не удаляя volume базы.
2. Создать дополнительную аварийную копию текущей базы.
3. Проверить backup восстановлением во временную БД.
4. Восстановить целевую БД только после успешной проверки.
5. Запустить миграции того release, которому соответствует backup.
6. Проверить readiness и основные сценарии до открытия доступа пользователям.

Никогда не восстанавливать backup поверх production без подтверждённого пути отката.

## Приёмочный минимум

- readiness полностью зелёный;
- версия API соответствует release;
- schema revision совпадает с `app/schema.py`;
- серверные тесты проходят полностью;
- оригиналы Google Drive не изменяются;
- внешние задачи, письма и события создаются только после предусмотренного подтверждения;
- Telegram `getMe` успешен, Gemini возвращает три варианта ответа;
- журнал содержит изменения AI-политики и обращения к внешней модели без текста документов.
# Проверка и восстановление резервной копии

Production deployment не только создаёт custom-format dump, но и полностью
восстанавливает его с `pg_restore --no-owner` во временную изолированную
PostgreSQL. Переключение релиза запрещено, если restore или проверка таблиц
завершились ошибкой.

При переносе на другую инфраструктуру сначала создайте пустую БД и роль
назначения, затем выполните `pg_restore --no-owner --role=<роль> -d <база>
<backup.dump>`. Не проверяйте восстановление поверх production-БД.

## REVIEW-ONLY: inert Alembic proposal for MaterialReceipt (PR421)

> **Статус: только для ревью. Не исполняемая документация.**
> Этот раздел — инертное предложение. Ревизия **не установлена** в
> `backend/migrations/versions`, никакие команды `upgrade`/`downgrade` или
> production-команды не выполнялись, одобрения на rollout нет. Локальный venv
> сейчас не содержит Alembic. Модель `backend/app/models/purchase.py` и активные
> миграции/процессы не изменялись.

Базовый родитель проверен для PR421: `86a4b2b585473fc31fdda44f595965f3c0c82bd2`.
Перед интеграцией необходимо повторно сверить целевой head Alembic.

Предлагаемая ревизия (полный пример, только для статического сравнения с
`MaterialReceipt`):

```python
"""material_receipts (review-only proposal, not installed)

Revision ID: c0d1e2f3a5b6
Revises: b9c0d1e2f3a4
"""

from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a5b6"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("purchase_item_id", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=160), nullable=False),
        sa.Column("qty", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("source_document_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_item_id"], ["purchase_items.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id", "operation_key",
            name="uq_material_receipt_project_operation",
        ),
        sa.CheckConstraint("qty > 0", name="ck_material_receipt_qty_positive"),
    )
    op.create_index(
        "ix_material_receipts_project_id", "material_receipts", ["project_id"],
    )
    op.create_index(
        "ix_material_receipts_purchase_item_id",
        "material_receipts",
        ["purchase_item_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing destructive downgrade of material_receipts: "
        "pending separate data-retention review. No DROP operations are performed."
    )
```

Замечания для ревьюера:

- `upgrade` создаёт только таблицу `material_receipts` и два ORM-индекса
  (`project_id`, `purchase_item_id`); имена ограничений —
  `uq_material_receipt_project_operation` и `ck_material_receipt_qty_positive`.
- FK на `projects` — `CASCADE`, FK на `purchase_items` — `RESTRICT`.
- `downgrade` намеренно отказывает через `RuntimeError` и не содержит `DROP`.
- Реальная проверка Alembic в изолированном PostgreSQL и порядок
  «сначала схема, потом приложение» по-прежнему обязательны, поскольку
  предложение по перестройке purchase читает новую таблицу.
- Вне области: доступ к БД, установки, провайдеры, сервисы, секреты, платежи,
  боты, реализация исходного кода, политика отрицательных остатков, списания
  и конвертация единиц.
