# Phase 1b — фактическая развязка MVP-1

Дата: 2026-09-09

База: `2bf9490fc6b66559453ca318c96854e97dd68bbb`

Scope: только точки соприкосновения MVP-1; Phase 2 не начата.

## Что стало отделено

- Добавлен самостоятельный ASGI entrypoint `app.app_mvp1:app` с явной композицией `MVP-1 ONLY`.
- MVP-1 entrypoint не импортирует aggregate `app.main`, Tasks, response drafts, governance, execution finance, contacts и messages.
- Общий registry моделей получил scope `PU_MODEL_SCOPE=mvp1`; полный registry для legacy app и Alembic сохранён.
- Вызовы создания Tasks, response drafts и governance items из `workspace.py` и `organizer.py` вынесены в `mvp1_extensions`.
- В MVP-1 установлен явный no-op режим; полный legacy app сохраняет прежнее поведение.
- Telegram notification вынесен в тот же optional bridge; совместимый символ оставлен без прямого Telegram-импорта.
- Агрегаты Tasks/Governance у документа и Finance/Contacts/Messages у readiness проекта получаются через optional bridge и равны нулю в MVP-1.
- Импорты чужих моделей в `organizations_contracts.py` выполняются только при включённых расширениях; finance/control и contract-analysis routes не включаются в MVP-1 app.
- Ресурсы получили верхнеуровневые aliases без переписывания бизнес-логики:
  - `/proposals`;
  - `/rules`;
  - `/rollbacks/{proposal_id}`;
  - `/change-batches` и `/change-batches/{proposal_id}`;
  - apply/rollback для change batch.
- MVP-1 readiness отделён от Gmail/AI Secretary automation readiness и остаётся fail-closed по обязательным секретам, БД и Alembic revision.

## Что осталось entangled и почему

- `organizations_contracts.py` остаётся физически общим модулем. Разделение выполнено на import/router boundary; перенос бизнес-логики запрещён условием Phase 1b.
- `workspace.py` и `organizer.py` используют существующие durable jobs, organizer repository/executor и storage adapter. Это исполняющая инфраструктура MVP-1, а не Tasks/Governance/Finance.
- Google OAuth router не включён в чистую MVP-1 композицию: текущий callback связан с durable mailbox identity следующего MVP. В MVP-1 остаётся provider-neutral `/projects/{id}/drive` reference binding; выделение OAuth port требует отдельного согласованного этапа.
- Local upload router не включён: его текущий import graph включает Materialization/v5.4 staging. Файлы не удалены, legacy app не изменён.
- Полный `app.main` намеренно остаётся композицией всех MVP для обратной совместимости. Изолированный запуск выполняется через `uvicorn app.app_mvp1:app`.
- PostgreSQL runtime отдельно не запускался в этой фазе; миграции не менялись.

## Изменённые файлы

- `backend/app/app_mvp1.py`
- `backend/app/core/mvp1_readiness.py`
- `backend/app/mvp1_extensions.py`
- `backend/app/models/__init__.py`
- `backend/app/api/projects.py`
- `backend/app/api/documents.py`
- `backend/app/api/organizations_contracts.py`
- `backend/app/api/workspace.py`
- `backend/app/organizer.py`
- `backend/app/main.py`
- `backend/tests/test_mvp1_phase1b_isolation.py`
- `docs/audits/mvp1-phase1b-decoupling.md`

## Проверки

- Новый isolation contract: `5 passed`.
- Исправленная compatibility-группа: `14 passed`.
- Договорной targeted regression: `15 passed`.
- MVP-1 targeted regression: `25 passed`.
- Полный backend: `2488 passed, 64 skipped`; одно первоначальное падение совместимости исправлено и перепроверено.
- Импорт полного app/model registry: PASS (`242` routes, `Task` доступен).
- Импорт MVP-1 в отдельном процессе: PASS (`0` запрещённых later-MVP modules).
- Alembic: одна head `a54f001c0a21`.
- Миграции, product data, production, push, merge и deploy не выполнялись.

## Критерии Phase 1b

| Критерий | До | После |
|---|---|---|
| Побочные действия следующих MVP в анализе MVP-1 | eager/direct | explicit optional bridge; MVP-1 no-op |
| Router composition | единый full app | отдельный `app_mvp1.py` |
| Model import graph | любой submodule загружал весь registry | MVP-1 scoped registry |
| Чужие агрегаты в Projects/Documents | прямые ORM imports | optional query bridge |
| Finance/Tasks/Governance в Contracts | eager imports/routes | conditional imports; routes исключены из MVP-1 |
| Phase 1 resources | только `/organizer/*` | canonical aliases + legacy paths |

Phase 2 не начата. Требуется явное подтверждение перехода.
