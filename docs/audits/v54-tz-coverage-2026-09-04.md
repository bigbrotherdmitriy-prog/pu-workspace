# PU Workspace v5.4 — покрытие MVP5 и оставшиеся условия приёмки

Дата актуализации: 2026-09-04

Проверенный интеграционный кандидат: `codex/v54-wave3-integration`

Проверенный HEAD: `7758bdd`

Текущая единственная Alembic head: `a54f001c0a08`

Решение: **MVP5 — 88% по критериям ТЗ; Wave3 runtime CONDITIONAL; production enable BLOCKED**

## Вывод

В текущем кандидате собраны основные контуры MVP5: durable mailbox identity,
зашифрованное staging-хранилище, Evidence API/UI, Context Graph пилота,
CONFIRM-путь, узкий policy-authorized AUTO для внутренней задачи, Action Ledger,
идемпотентность и provider reconciliation. Wave2 прошёл изолированный Linux и
PostgreSQL CI. Wave3 добавил схемы `a05`–`a08`, local upload и Gmail attachment
wiring, поэтому зелёный Wave2 нельзя считать runtime-доказательством Wave3.

Продуктовая компенсация необратимого email и два security blocker local upload
закрыты кодом и regression-тестами. До полного MVP5 остаются product-like
проверка полного deadline/external-confirm сценария и зелёный PostgreSQL/
process-fault CI на итоговой Wave3 head. До этого нельзя заявлять ни production
readiness, ни live-provider acceptance.

## Источник и границы оценки

Источник требований — read-only файл
`PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`. Имя файла содержит
`v5_4`, тогда как титульная часть и §§31/40 называют документ версией 5.1. В
этом отчёте «v5.4» означает текущий пакет работ; перед внешней передачей владельцу
нужно утвердить одно каноническое обозначение версии.

Оценка MVP5 основана на двух явных наборах требований документа:

1. шесть признаков `MVP5 Pilot Ready` из §31.8: evidence-backed end-to-end
   Communication-to-Action, пилотный Context Graph, CONFIRM для критических
   действий, Action Ledger, idempotency/deduplication и базовая классификация
   reversible/compensatable/irreversible;
2. семь дополнительных сценариев A–G из §31.9.

`Company Memory` из §31.5 не включена в процент: §31.8 прямо относит её
расширенную реализацию к **1.0+**, вместе с дополнительными Agents, richer
Context Graph и enterprise policy automation. Product Scope остальных разделов
также не считается автоматически включённым в MVP5.

## Метод расчёта процента

Для каждого из 13 критериев применяется одна шкала:

- `1` — код интегрирован в проверенный HEAD и есть профильная автоматическая
  проверка;
- `0,5` — безопасная часть интегрирована, но обязательная часть сценария или
  runtime-доказательство отсутствует;
- `0` — требуемый продуктовый результат ещё не интегрирован.

Расчёт: `(5,5 балла по §31.8 + 6 баллов по §31.9) / 13 × 100 = 88,5%`,
округлённо **88%**.

Этот процент показывает покрытие критериев MVP5, а не вероятность отсутствия
дефектов. Production readiness является отдельным бинарным gate и сейчас не
пройдена.

## Матрица определения MVP5 Pilot Ready

| Критерий §31.8 | Балл | Статус | Фактическое доказательство | Остаток |
|---|---:|---|---|---|
| Evidence-backed end-to-end Communication-to-Action | 0,5 | PARTIAL | Source/Evidence pins, Context, Trust, Task, receipt/audit, Evidence UI и отдельный corrective email action соединены; local/Gmail ingress используют a05 lifecycle | Полный deadline/external-confirm путь ещё не принят как единый product-like сценарий; Wave3 PostgreSQL runtime не пройден |
| Context Graph для пилотного сценария | 1 | CODE PASS | Hypothesis/confirm/correct с CAS, историей, защитой от late analysis и receipt projection | Более богатый enterprise graph относится к 1.0+, не к MVP5 |
| CONFIRM для критических действий | 1 | CODE PASS | Точная approval binding, payload/revision/hash invalidation, live Authority перед T2; service worker и global admin не получают обход | Live external provider acceptance остаётся отдельным gate |
| Action Ledger | 1 | CODE PASS | Append-oriented audit, sealed action, approval/policy origin, execution attempt, outcome observation и receipt | Production-readable полный ledger UI не требуется для зачёта этого узкого пилотного критерия |
| Idempotency и deduplication | 1 | CODE PASS | Mailbox-scoped dedup, stable command/job binding, один Task/receipt/projection, provider UNKNOWN → lookup вместо повторного dispatch | Конкурентность новых a05–a08 должна подтвердиться Wave3 PostgreSQL CI |
| Reversible/compensatable/irreversible classification | 1 | CODE PASS | Тип действия входит в sealed envelope; отмена Task является новым действием; irreversible provider outcome не переписывается | UX для компенсирующего email учитывается отдельно в сценарии E |
| **Итого §31.8** | **5,5 / 6** |  |  |  |

## Матрица дополнительных сценариев A–G

| Сценарий §31.9 | Балл | Статус | Фактическое доказательство | Остаток |
|---|---:|---|---|---|
| A. Срок вместе с evidence на точный источник/версию; без evidence — unverified | 0,5 | PARTIAL | Read-only Evidence API/UI показывает exact source/version/locator, confidence и human assessment; stale/denied content скрывается | Browser path показывает evidence, но не завершённый продуктовый договорный deadline flow |
| B. `create-internal-task=AUTO`, `send-external-message=CONFIRM` | 0,5 | PARTIAL | a07 разрешает только low-risk `task.internal.create` через точную SERVER_POLICY без фиктивного human approval; внешние действия не становятся AUTO | External message остаётся synthetic/default-off; live/product send не принят |
| C. Изменённый payload инвалидирует старое approval | 1 | CODE PASS | Approval привязан к action/revision/envelope/payload hash; негативные T2 tests блокируют подмену до эффекта | Требуется только общий Wave3 runtime gate |
| D. Reversible Task отменяется отдельным audited action | 1 | CODE PASS | `task.internal.cancel` имеет отдельные action, permission/approval, receipt и audit; исходная история сохраняется | Нет остатка в границе synthetic pilot |
| E. Для отправленного email UI предлагает новый compensating follow-up | 1 | CODE PASS | Исходный APPLIED email неизменяем; API/UI создают отдельный FROZEN corrective draft с новым CONFIRM, exact source/outcome/mailbox/project/evidence pins и fail-closed recheck | Live send намеренно не выполняется до отдельной provider acceptance |
| F. Source остаётся у клиента; запрещённая копия не создаётся | 1 | CODE PASS | a05 связывает SourceVersion/Evidence/Materialization, staging шифрует представление, payload содержит только `staging_id`; a08 добавляет service retention purge и DB/lease fence от дублей | Требуется PostgreSQL fault-runtime доказательство a08 перед enable |
| G. Недоступный или устаревший provider source явно маркируется | 1 | CODE PASS | Fail-closed resolver и EvidencePanel показывают unavailable/stale без выдачи fragment; late reply не возвращает старый проект | Live provider outage ещё не проверен |
| **Итого §31.9** | **6 / 7** |  |  |  |

## Реализованная последовательность схемы

| Ревизия | Назначение | Состояние в кандидате |
|---|---|---|
| `a54f001c0a03` | Durable mailbox identity и credential generations | INTEGRATED |
| `a54f001c0a04` | Mailbox-scoped dedup и cutover constraints | INTEGRATED |
| `a54f001c0a05` | SourceVersion-bound materialization lifecycle | INTEGRATED |
| `a54f001c0a06` | Provider action, approval, outbox, attempt и outcome schema | INTEGRATED |
| `a54f001c0a07` | Явный HUMAN_APPROVAL/SERVER_POLICY authorization origin | INTEGRATED |
| `a54f001c0a08` | Service retention purge и DB/lease fence local materialization | CURRENT HEAD |

Все runtime/readiness pins проверенного HEAD ожидают `a54f001c0a08`. Email
compensation не потребовала миграции; `a08` является единственной
последовательной миграцией после `a07`.

## Фактическое покрытие Wave2 и Wave3

Профильные доказательства: [mailbox identity](v54-mailbox-identity-implementation.md),
[rollout controls](v54-mailbox-rollout-controls.md),
[Evidence API/UI](v54-evidence-product-api-ui.md),
[local upload staging](v54-local-upload-staging.md),
[Gmail attachment wiring](v54-gmail-a05-wiring.md),
[provider runtime](v54-provider-action-runtime.md),
[autonomy authorization](v54-autonomy-authorization.md),
[security review](v54-wave3-security-review.md) и
[Wave3 CI gate](v54-wave3-ci-gate.md).

### Wave2 runtime

Wave2 commit `f721634762944e8bf9020e99c50f504678291296` получил зелёные
изолированные GitHub Actions:

- `v5.4 runtime`, run `33849135378` — PASS;
- `Docker smoke`, run `33849135528` — PASS;
- `PU Workspace CI`, run `33849135679` — PASS.

Этим подтверждены Linux runner, чистая PostgreSQL, миграции до Wave2 head
`a54f001c0a04`, профильные PostgreSQL/process-fault проверки, сборка и cleanup
в границах тех workflow. Это не подтверждает миграции и новые конкурентные пути
Wave3 `a05`–`a08`.

### Wave3 code и тесты

- mailbox `a03/a04`: verified Google OIDC `sub`, durable credential generation,
  append-only origin, mailbox authority, scoped rollout lattice и CAS;
- encrypted staging `a05`: AES-256-GCM, opaque ID, SourceVersion/Evidence/
  Materialization binding, exact worker lease и job payload только
  `{"staging_id": "..."}`;
- local upload: admission → encrypted staging → существующий BackgroundJob →
  guarded processing → durable terminal state → cleanup;
- Gmail attachment: mailbox authorization до provider read и каждого staged
  read, a05 binding, один provider open, terminal cleanup/recovery;
- Evidence API/UI: server-derived ACL/policy, exact evidence/version/locator,
  content-free unavailable projection и manual-review presentation;
- provider `a06`: durable outbox/attempt/outcome, `UNKNOWN` reconciliation без
  слепого повторного эффекта и safe audit;
- autonomy `a07`: owner-managed CAS policy, узкий internal-task AUTO через
  SERVER_POLICY, live recheck перед T2; external/finance/legal/destructive AUTO
  запрещены;
- staging safety `a08`: service-authorized `EXPIRED → delete → PURGED`, exact
  claim fence перед legacy commits и partial unique identity local Document;
- email compensation: неизменяемый исходный outcome, отдельные corrective
  action/draft, новый CONFIRM и защита от stale source observation;
- browser E2E: пять synthetic Playwright-сценариев на реальном `App` покрывают
  readable/manual-review evidence, stale/denied fail-closed, late-reply без
  возврата к старому проекту, attachment import UI и local-upload/AI-policy
  маршруты. Это не live-provider и не browser→PostgreSQL→worker доказательство.

Последний объединённый adversarial прогон перед a07 зафиксировал `1087 passed,
15 skipped`; профильный a07 regression — `87 passed, 1 PostgreSQL skip`;
локальный Wave3 CI contract — `26 passed`. Эти результаты подтверждают кодовые
контракты, но не заменяют итоговый PostgreSQL fault run на итоговом Wave3 HEAD.

## Оставшиеся обязательные gate

### 1. Wave3 PostgreSQL и process-fault CI

Нужно запустить на точном итоговом SHA оба изолированных workflow:

- `v54-pilot-runtime.yml` — upgrade чистой PostgreSQL до `a54f001c0a08`, A/B/C,
  Authority/Context/Source CAS, process crash, lease recovery, единственность
  Task/receipt/audit/projection и cleanup;
- `durable-queue.yml` — два API, два worker, scheduler, retry/dead-letter/
  redrive/cancel, backup/restore и cleanup.

До зелёных artifacts итог остаётся `CONDITIONAL`, даже при полностью зелёных
локальных unit/integration тестах.

### 2. Email compensation — закрыто кодом

Сценарий E реализован как безопасный продуктовый результат:

1. отправленное письмо помечается `IRREVERSIBLE` и не предлагает фиктивный undo;
2. UI предлагает создать новое corrective follow-up action;
3. follow-up получает новый immutable payload, новый approval/policy decision,
   отдельный idempotency key, receipt и audit;
4. исходный email/outcome не переписывается;
5. synthetic browser/API tests подтверждают поведение без реальной отправки.

### 3. Два P1 blocker перед включением staging — закрыты кодом, ждут runtime

[Wave3 security review](v54-wave3-security-review.md) запретил production-enable
до закрытия двух проблем. Коммит hardening и его regression-тесты реализовали:

1. **Нет независимого retention recovery для failed/dead-letter local
   materialization.** После исчерпания попыток ciphertext может пережить
   `retention_until`. Нужен service-authorized bounded scanner с порядком
   `durable EXPIRED → idempotent delete → durable PURGED`, работающий и после
   отзыва пользовательского grant.
2. **Lease fence не охватывает legacy business commits.** Старый attempt может
   продолжить commit после потери heartbeat, а новый attempt — начать обработку;
   таблица `documents` не гарантирует уникальность stable local source. Нужен
   DB-enforced document identity или durable operation/claim fence до первой
   идемпотентной бизнес-точки.

Runtime composition остаётся default-off/fail-closed до PostgreSQL fault-проверки
`a08`. Gmail attachment path также нельзя объявлять live-ready без итогового
Wave3 PostgreSQL и mailbox/provider acceptance.

## Что не входит в оставшийся MVP5

Следующие функции не должны увеличивать remaining MVP5 scope:

- расширенная Company Memory;
- дополнительные AI Agents;
- richer enterprise Context Graph;
- enterprise policy automation;
- новые live providers сверх пилотного пути;
- финансовые, юридические, платёжные и destructive AUTO actions;
- production deployment.

Они относятся к MVP6 или 1.0+ либо требуют отдельного решения владельца.

## Финальный readiness checklist

| Gate | Статус сейчас | Условие закрытия |
|---|---|---|
| 13 критериев MVP5 | **88%** | Подтвердить A/B единым product-like acceptance и Wave3 runtime |
| Единственная Alembic head | PASS | Сохранить линейную head `a08` |
| Wave2 Linux/PostgreSQL runtime | PASS | Уже подтверждён на `f721634` тремя зелёными runs |
| Wave3 Linux/PostgreSQL runtime | CONDITIONAL | Зелёные runtime и durable-queue artifacts на точном финальном SHA |
| Staging security enable | CONDITIONAL | Код/регрессия готовы; нужен PostgreSQL fault runtime a08 |
| Email compensation | CODE PASS | Новый corrective follow-up action и UI/API tests готовы; live send не выполнялся |
| Live provider acceptance | NOT RUN | Изолированная тестовая учётная запись, exact mailbox, no duplicate effect, reconciliation |
| Production enable | BLOCKED | Все предыдущие gates закрыты и выдано отдельное разрешение владельца |

## Следующий безопасный порядок

1. Завершить product-like synthetic acceptance полного A/B пути.
2. Запустить полный backend/frontend/browser contract и проверить одну Alembic
   head.
3. Выполнить оба изолированных Wave3 GitHub workflow и проверить safe JSON
   artifacts и cleanup.
4. После зелёного CI провести отдельный live-provider sandbox acceptance.
5. Не выполнять merge или production deploy до отдельного решения владельца.

## Итоговый статус

Кандидат реализует **88% явных критериев MVP5** и является сильным
Wave3 code candidate. Он ещё не является production-ready: product-like полный
A/B путь и PostgreSQL/process-fault CI не приняты на схеме `a54f001c0a08`.
Company Memory
не является недостающим пунктом MVP5 и не должна задерживать его закрытие.
