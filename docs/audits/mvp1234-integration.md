# Интеграция MVP1 + MVP2 + MVP3 + безопасных срезов MVP4

Дата проверки: 2026-09-05

Ветка: `codex/mvp1234-integration`

База: `48ad2ca2184f11fcfba2aff46fb95b6a5b68d601`

## Результат

Поверх общей базы MVP1, MVP2 и M4-06 содержательно интегрированы:

- `80188920f07042b77c8259b7db59e6e14a4ca3e9` — evidence-backed lifecycle
  обязательств, задач, сроков, рисков и решений;
- `a29432d5eb8915441e29a49a24a5717738ef6d65` — immutable Evidence для
  реквизитов договора;
- `28687a0936ccabcf3f7b6240fc474e5e48696f21` — безопасные предложения по
  встречам/сообщениям и внутренний management digest;
- `b5e3fafd460e88b8ab94042c8c86ffc892a43bf9` — ранее выполненная приёмка
  общей базы MVP1 + MVP2 + M4-06.

Ветка сохраняет одну существующую PostgreSQL-очередь `BackgroundJob`,
fail-closed Source/Evidence проверки, CAS и append-only history. Автоматические
внешние действия и автоматическое подтверждение оплаты не включены.

## Карта пересечений и разрешение

Текстовых cherry-pick-конфликтов не возникло. Пересекающиеся участки всё равно
проверялись как содержательные интеграционные границы:

| Область | Что сохранено |
| --- | --- |
| `organizations_contracts.py`, `test_contracts_api.py` | MVP1 edit/archive/delete guards и M4 exact SourceVersion/Evidence binding, conflict/manual-review поведение |
| `management.py`, governance/management models | legacy compatibility плюс новый CAS lifecycle, evidence pins, review state и append-only histories |
| `jobs/handlers.py` | все существующие durable handlers; добавлен только `mvp3.management_digest` с content-free scalar payload |
| schema/runtime/workflow/Compose pins | единая последовательная head `a54f001c0a10`, ожидания readiness и runtime обновлены на неё |
| frontend `App.tsx` и модули | одновременно сохранены storage picker, contracts, AI Secretary и finance UI; последующие MVP3/M4 evidence-коммиты frontend не меняли |

## Инварианты общей ветки

- snapshot исходной папки не запускает safe-copy автоматически;
- анализ без копии и safe-copy/standardization являются разными явными
  durable-командами;
- договор можно редактировать и архивировать без удаления связанных документов;
- неоднозначное письмо не создаёт задачу/риск/черновик до подтверждения контекста;
- утверждение черновика требует manager и сбрасывается при любом редактировании;
- обязательство, риск и решение требуют exact current Evidence и используют CAS;
- внутренняя Task создаётся идемпотентно только после human confirmation;
- meeting/message extraction создаёт предложения, а не исполненные действия;
- digest является внутренним агрегатом без документов, писем и excerpts;
- реквизиты договора не записываются при low confidence, конфликте или отсутствии
  exact `DocumentVersion -> SourceVersion -> SourceCurrent` связи;
- оплату подтверждает manager; несовпадающий повтор получает `409`, исправление
  создаётся отдельной корректировкой;
- второй queue, graph, ledger или evidence registry не создавался.

## Проверки

| Проверка | Результат |
| --- | --- |
| MVP1–MVP4 targeted после foundation | `134 passed` |
| M4 evidence + MVP3 meeting/digest + integration targeted | `62 passed` |
| Финальный полный backend pytest | `1198 passed, 19 skipped` |
| Durable queue и CI contract tests | `81 passed` |
| Полный frontend Vitest | `102 passed` |
| Frontend TypeScript check | PASS |
| Frontend production build | PASS |
| Alembic heads | одна: `a54f001c0a10` |
| `CURRENT_SCHEMA_REVISION` и runtime pins | `a54f001c0a10` |
| Скан изменённых workflow/scripts на private keys, OAuth/refresh tokens, hardcoded DSN/password | PASS, совпадений нет |
| `git diff --check` | PASS |

Frontend build-output `backend/app/react_dist` восстановлен и в интеграционные
коммиты не попал. Из-за поведения Windows ACL после security-тестов временные
каталоги pytest могут быть недоступны для немедленного удаления; общий шаблон
`.pytest-*/` добавлен в `.gitignore`, поэтому они не попадают в Git или release
bundle. Для повторного полного прогона использована новая изолированная область.

Docker CLI и `actionlint` в локальном окружении отсутствуют. Поэтому Compose
runtime и actionlint не объявляются выполненными. Workflow/queue контракты
проверены исполняемыми Python-тестами, но не подменяют runtime.

## Alembic

Итоговая цепочка линейна:

```text
a54f001c0a09 -> a54f001c0a10
```

`a54f001c0a10` добавляет management lifecycle поля и таблицы
`obligation_history`, `governance_history`. Offline migration SQL и единственная
head проверены тестами. Upgrade на отдельной реальной PostgreSQL в этой worktree
не выполнялся.

## Оставшиеся задачи MVP

### MVP1

- live OAuth acceptance на тестовых Google Drive/Яндекс Диск аккаунтах;
- provider-native revision, rename/move/rollback и delta/latency на 1k/10k
  объектов;
- browser E2E с живыми picker/API.

### MVP2

- live Gmail ingress/history/pagination и credential-generation acceptance;
- перевод legacy Gmail send, Google Tasks и Calendar на durable provider-action
  outbox, включая UNKNOWN reconciliation;
- restart/lease acceptance encrypted attachment staging;
- долговечная multi-project Company модель и live browser E2E ролей.

### MVP3

- подключение нового meeting proposal сервиса через API/UI и вывод из
  эксплуатации legacy auto-create после миграции клиентов;
- durable cadence и хранимые preferences для digest/deadline escalation;
- frontend для attention, approvals, risks/decisions и conflict correction;
- единая Company/Person/Contact identity и permission-filtered project search;
- PostgreSQL concurrent CAS и итоговая synthetic/browser/runtime acceptance.

### MVP4

Закрыты только foundation M4-02 и безопасный M4-06. Остаются утверждённые
владельцем финансовые DTO/пороги, immutable версии ГПР/поставок/актов, полный
plan/fact и currency/rounding/CAS, закупочная цепочка, explainable forecast,
reversal/permissions/concurrency и юридическая/эксплуатационная приёмка.

## Решение

**PASS** для локальной интеграции реализованных срезов MVP1–MVP4.

**CONDITIONAL** для PostgreSQL runtime, Docker/Compose и живых внешних
провайдеров. Полностью завершёнными все MVP1–MVP4 этим отчётом не объявляются.

Production, DNS, production БД, OAuth/почтовые учётные данные и пользовательские
документы не изменялись. Push, merge и deploy не выполнялись из-за действующей
EU cutover freeze.
