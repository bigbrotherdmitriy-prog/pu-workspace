# PU Workspace — карта реализации ТЗ v5.1

## Принятое решение

- ТЗ `PU_Workspace_TZ_v5_1_IMPLEMENTATION_READY.docx` — основной источник требований.
- FastAPI, SQLAlchemy, Alembic и PostgreSQL сохраняются как ядро.
- React/Vite из предоставленного UI-архива используется как основа нового web-интерфейса.
- Express, tRPC, Drizzle, MySQL и Manus OAuth из архива не переносятся.
- Старый интерфейс остаётся доступным до ручной приёмки React-интерфейса.

## Карта существующих модулей

| Контур v5.1 | Текущая реализация | Состояние |
|---|---|---|
| User / RoleAssignment | `models/user.py`, `project_member.py`, `api/access.py` | частично |
| Project | `models/project.py`, `api/projects.py` | есть, требует Contract |
| Google Integration | `google_token.py`, `api/google_drive.py` | есть |
| SourceFolder | organizer session хранит source folder | частично |
| WorkspaceSnapshot | физическая безопасная копия | заменить виртуальным snapshot |
| VirtualNode | отсутствует | P0 |
| Document / Version | `document.py`, `document_version.py`, `document_engine.py` | в разработке |
| ExtractionResult | извлечение есть, отдельной сущности/статуса нет | P0 |
| Rule / Proposal | organizer rules/proposals/actions | частично |
| ChangeBatch / Operation | proposal/operation | нужны version + idempotency key |
| Dry-run | проверки безопасной копии | нужен source recheck |
| Conflict | отсутствует `conflict_source_changed` | P0 |
| AuditEvent | audit log + organizer operations | частично |
| RollbackRun | rollback операций | требуется отдельный запуск/статус |
| Recovery | восстановление незавершённых scan | частично |

## Технический долг

1. Текущий MVP физически копирует дерево Drive; v5.1 требует snapshot метаданных и virtual tree.
2. Нет Contract и Organization как центральных сущностей первого среза.
3. Нет повторной проверки `name`, `parent_ids`, `modifiedTime/revision` перед apply.
4. Нет idempotency key на ChangeBatch и операциях.
5. ExtractionResult не хранит явные processed/skipped/failed и причину ошибки.
6. Web/API и worker пока не разделены на процессы; ThreadPool — временное решение.
7. Уже подключённые Telegram/Tasks/Calendar сохраняются, но не расширяются до приёмки MVP-1.

## Порядок ближайших изменений

1. Внедрить React shell и подключить существующие read-only API.
2. Добавить Organization, Contract, WorkspaceSnapshot, VirtualNode, ExtractionResult.
3. Перевести анализ на virtual snapshot без массового копирования.
4. Добавить dry-run, idempotency и конфликт источника.
5. Провести acceptance и performance smoke test на 1 000 метаданных объектов.
