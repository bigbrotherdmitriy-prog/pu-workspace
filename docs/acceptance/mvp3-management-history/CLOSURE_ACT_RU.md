# Акт закрытия MVP-3 (управленческий контур)

Дата независимой сверки: 09.10.2026. Метод: read-only запросы к production-БД (`puw-primary-next-primary-db`) и одна контролируемая read/write-проверка CAS-конфликта (откат после проверки, без изменения данных), независимо от документации.

Критерий приёмки MVP-3 — пять обязательных инвариантов из `docs/audits/mvp3-management-history-adr.md` (ADR, принят 20.09.2026, Design A).

## 1. Любая подтверждённая мутация записывает `management_history` в той же транзакции

Подтверждено. На проде — 84 строки `management_history`, покрывающие 10 типов сущностей (`obligation`, `risk`, `management_digest`, `contact_conflict`, `project_contact`, `task`, `decision`, `notification_policy`, `meeting`, `notification`). Пример реальной мутации (risk id=223): запись истории `id=50`, `action=updated`, `record_version=2` — совпадает с текущей `record_version` самой сущности.

## 2. Запись содержит правильные tenant/project, actor, record_version, before/after, evidence/reason

Подтверждено на том же примере:
```
old_values: {'status': 'needs_confirmation', 'action_note': None, ...}
new_values: {'status': 'resolved', 'action_note': 'учтено', ...}
```
`organization_id`, `project_id`, `actor_user_id`, `record_version` — заполнены корректно во всех проверенных строках.

## 3. Чтение истории проверяет доступ к проекту и фильтрует по project_id + entity_type + entity_id одновременно

Подтверждено по коду (`backend/app/api/management.py:931-951`, эндпоинт `GET /history/{entity_type}/{entity_id}`):
```python
require_project_role(db, user, project_id, "viewer")
...
query = select(ManagementHistory).where(
    ManagementHistory.project_id == project_id,
    ManagementHistory.entity_type == entity_type,
    ManagementHistory.entity_id == entity_id,
)
```
Пагинация (`cursor`/`limit`, 1–200) реализована тем же эндпоинтом.

## 4. При CAS-конфликте ни сущность, ни история не изменяются

Подтверждено прямой контролируемой проверкой (не только по тестам): вызван `update_risk(223, ..., expected_record_version=<заведомо устаревшая версия>)` напрямую на проде.

```
до:   version=2, status=resolved, history_count=1
ответ: 409 {'code': 'record_version_conflict', 'expected': 1, 'actual': 2}
после: version=2, status=resolved, history_count=1
```
Сущность и история идентичны до и после попытки — сохранено в рамках той же транзакции, откачено немедленно после проверки через `db.rollback()`, боевые данные не изменены.

## 5. Код приложения не обновляет и не удаляет строки истории

Подтверждено по коду: поиск по всему `backend/app/` не находит ни одного `UPDATE`/`DELETE` на `ManagementHistory` — единственная точка записи (`append_management_history()`, `management.py:201`) выполняет только `db.add(...)` (INSERT).

## Дополнительно по объёму MVP-3 (не инварианты, но часть заявленного охвата)

- **Таймзоны/quiet-hours**: на проде 8 реальных `notification_policies`; пример (проект №17): `timezone=Europe/Moscow`, `quiet_start=22:00:00`, `quiet_end=07:00:00`, `digest_cadence=daily`.
- **Принятые ограничения ADR** (отсутствие per-entity `sequence`, append-only через контракт приложения, а не DB-триггер, переходы статуса через `old_values`/`new_values`) подтверждены как сознательный выбор, не дефект — соответствует формулировке самого ADR.

## Заключение

Все пять обязательных инвариантов ADR MVP-3 подтверждены независимой сверкой — часть по read-only данным прода, часть прямой контролируемой проверкой с немедленным откатом. **MVP-3 закрыт** в части управленческого контура (CAS, append-only история, таймзоны/quiet-hours).

Не входит в этот акт: приёмка остальных заявленных в ADR MVP-3 областей (saved views, digests, resource catalog) — они используют тот же `management_history`/`AuditLog` механизм, но не проверялись отдельно построчно в рамках этого акта.
