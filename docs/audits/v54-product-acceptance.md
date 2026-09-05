# PU Workspace v5.4 — product-like synthetic acceptance

Дата: 2026-09-04

Ветка: `codex/v54-product-acceptance`

База аудита: `888c13616fc2bbef996d126180b93b3215847e4e`

## Решение

**SYNTHETIC PRODUCT ACCEPTANCE PASS / LIVE PROVIDER NOT RUN / PRODUCTION OFF.**

Добавлен один HTTP-bound сценарий, который проходит через FastAPI/TestClient и
реальные v5.4 сервисы, таблицы и существующий `BackgroundJob`. Он не использует
OAuth, Gmail API, реальные адреса, документы или production database.

Endpoint `/api/v54/sandbox/acceptance` недоступен по умолчанию. Для вызова
одновременно требуются:

1. `PU_V54_SYNTHETIC_ACCEPTANCE=true`;
2. явная dependency injection синтетического runtime;
3. точный заголовок `X-PU-V54-Synthetic-Acceptance: synthetic-v1`;
4. SQLite либо PostgreSQL database с именем `puw_v54_test_*`;
5. аутентифицированный пользователь.

Обычный product startup не устанавливает runtime. Даже одна environment
переменная не включает сценарий и не даёт provider authority.

## Аудит до изменения

Компоненты Source/Evidence, Context, DeadlineClaim, Trust, DB-backed Authority,
policy-authorized AUTO, provider outbox/reconciliation и email compensation уже
были реализованы. Однако единой product/API-композиции не существовало:

- `test_v54_pilot_integration.py` проверял внутреннюю задачу;
- `test_v54_autonomy_authorization.py` отдельно проверял AUTO;
- `test_v54_provider_action_runtime.py` отдельно проверял внешний effect;
- `test_v54_email_compensation.py` отдельно проверял corrective proposal.

Из-за разрыва один реальный дефект оставался незаметным: после успешного AUTO T2
`ContextCommunication.project_receipt()` всегда загружал `ActionApproval`.
SERVER_POLICY receipt по контракту не имеет approval, поэтому Task и receipt
фиксировались, но task relation projection падала как `resource_unavailable`.

Исправление разделяет историческую проверку origin:

- `HUMAN_APPROVAL` требует точный approval/action/revision/envelope binding;
- `SERVER_POLICY` требует отсутствие approval, точный ActionPolicy, hash,
  revision, authority epoch и enabling owner из неизменяемого policy document;
- неизвестный или смешанный origin закрывается отказом.

Новая очередь и миграция не создавались.

## Пройденный сценарий

1. Синтетическое входящее сообщение и attachment получают stable Source и
   SourceVersion.
2. Из attachment создаётся Evidence, затем выполняется отдельная human review.
3. Сообщение связывается с точным tenant/project/contract/mailbox; две Context
   relation подтверждаются с CAS.
4. Извлекается deadline, затем выполняется отдельная human review.
5. Owner заранее включает узкую политику `task.internal.create=AUTO`, при этом
   `message.external.send=CONFIRM`.
6. Внутренняя задача проходит T1, существующий BackgroundJob, T2 и создаётся
   ровно один раз без фиктивного human approval. Receipt имеет origin
   `SERVER_POLICY`; Context task relation успешно проецируется.
7. Внешнее synthetic message action имеет `CONFIRM`, exact approval и
   `IRREVERSIBLE`.
8. Инъекция timeout-after-effect создаёт `UNKNOWN`; второй worker выполняет
   lookup/reconciliation, а не повторный provider effect. Итог — `APPLIED`,
   effect count равен одному.
9. Для отправленного письма API сообщает «Отменить отправку нельзя» и предлагает
   новый corrective follow-up. Proposal остаётся `FROZEN`, получает новый action
   и требует отдельный `CONFIRM`; live send не вызывается.
10. HTTP-ответы проверяются на отсутствие body/excerpt/address/provider raw ID,
    DSN и traceback.

## Доказательства и команды

Основной тест:

`backend/tests/test_v54_product_acceptance.py`

CI hook:

`scripts/ci/v54_pilot_workflow.py`, phase `postgres_abc_integration`.

Фактические локальные результаты:

- новый black-box acceptance: `1 passed`;
- Context/Trust/AUTO/provider/compensation regression: `96 passed, 1 skipped`;
- полный backend: `1106 passed, 16 skipped`;
- профильный CI contract: `14 passed`;
- полный `scripts/ci`: `114 passed, 3 failed`; три существующих smoke
  assertions расходятся только в Unicode-представлении Windows/OneDrive пути
  (`Документы` против replacement characters). Они не исполняют и не проверяют
  изменённый v5.4 сценарий; профильный v5.4 CI contract прошёл отдельно.
- `git diff --check`: PASS.

Локальная команда:

```powershell
Set-Location backend
python -m pytest tests/test_v54_product_acceptance.py -q --tb=short
```

## Границы доказательства

- Это product-like synthetic acceptance, а не live Google/Gmail acceptance.
- Endpoint не является пользовательской функцией и production activation.
- SQLite прогон не доказывает PostgreSQL concurrency; CI hook должен пройти на
  изолированной PostgreSQL итоговой интеграционной ветки.
- Локальные PostgreSQL-only тесты пропущены из-за отсутствия выделенных test DSN;
  это не заменяется SQLite результатом.
- Реальные письма, provider credentials и документы не использовались.
- Внешний AUTO, финансовые, юридические и destructive actions остаются
  запрещены.
- Merge, push и deploy этим потоком не выполняются.
