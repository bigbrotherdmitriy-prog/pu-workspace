# PU Workspace v5.4 — покрытие MVP5 и оставшиеся условия приёмки

Дата актуализации: 2026-09-04

Проверенный интеграционный кандидат: `codex/v54-wave3-integration`

Проверенный продуктовый HEAD: `f0c0e26ef7cb971b2cef3965ec91b490471b127d`

Текущая единственная Alembic head: `a54f001c0a09`

Решение: **MVP5 code/contract scope — PASS по 13 критериям ТЗ; новый кандидат runtime CONDITIONAL до повторного CI; production enable BLOCKED**

## Вывод

В текущем кандидате собраны основные контуры MVP5: durable mailbox identity,
зашифрованное staging-хранилище, Evidence API/UI, Context Graph пилота,
CONFIRM-путь, узкий policy-authorized AUTO для внутренней задачи, Action Ledger,
идемпотентность и provider reconciliation. Wave2 прошёл изолированный Linux и
PostgreSQL CI. Wave3 добавил схемы `a05`–`a08`, local upload и Gmail attachment
wiring, поэтому зелёный Wave2 нельзя считать runtime-доказательством Wave3.

Продуктовая компенсация необратимого email, security blocker local upload и все
явные MVP5 gaps `C01`, `C07`, `S02`, `S07`, `S08` закрыты кодом и исполняемыми
contract-тестами. Runtime protocol нового кандидата оставляет только `P04`
(финансы вне MVP5) и `S10` (live provider не запускался).

Run `33865331595` подтвердил базовый commit `f869319`, схему `a54f001c0a08`,
`1123 passed / 15 skipped` backend, `293 passed / 0 failed` в
PostgreSQL-поднаборе, `process_reclaim=PASS` и cleanup. После этого run добавлены
новые process-fault probes, content pipeline, mailbox acceptance и миграция
`a54f001c0a09`; поэтому этот PASS нельзя переносить на `f0c0e26`. Новый кандидат
остаётся runtime CONDITIONAL до повторного CI. Подробная классификация приведена
в [сверке релизного gate](v54-wave3-release-reconciliation.md).

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

Расчёт: `(6 баллов по §31.8 + 7 баллов по §31.9) / 13 × 100 = 100%`.

Этот процент показывает наличие интегрированных контрактов и автоматических
проверок по критериям MVP5, а не полноту сквозной продуктовой приёмки и не
вероятность отсутствия дефектов. Product E2E, live-provider, production и
commercial readiness являются отдельными gates.

## Матрица определения MVP5 Pilot Ready

| Критерий §31.8 | Балл | Статус | Фактическое доказательство | Остаток |
|---|---:|---|---|---|
| Evidence-backed end-to-end Communication-to-Action | 1 | CODE/CONTRACT PASS | Synthetic C01 pipeline читает реальные corpus bytes, извлекает project/contract/deadline, создаёт exact Evidence и проводит результат через Context, Trust, Task и receipt | Новый PostgreSQL runtime на `f0c0e26` ещё не выполнен; production ingress намеренно не включён |
| Context Graph для пилотного сценария | 1 | CODE PASS | Hypothesis/confirm/correct с CAS, историей, защитой от late analysis и receipt projection | Более богатый enterprise graph относится к 1.0+, не к MVP5 |
| CONFIRM для критических действий | 1 | CODE PASS | Точная approval binding, payload/revision/hash invalidation, live Authority перед T2; service worker и global admin не получают обход | Live external provider acceptance остаётся отдельным gate |
| Action Ledger | 1 | CODE PASS | Append-oriented audit, sealed action, approval/policy origin, execution attempt, outcome observation и receipt | Production-readable полный ledger UI не требуется для зачёта этого узкого пилотного критерия |
| Idempotency и deduplication | 1 | CODE/CONTRACT PASS | Mailbox-scoped dedup, S02 multi-mailbox acceptance, stable command/job binding, S07/S08 process harness, один Task/receipt/projection, provider UNKNOWN → lookup | Требуется новый runtime; live `S10` остаётся отдельным provider gate |
| Reversible/compensatable/irreversible classification | 1 | CODE PASS | Тип действия входит в sealed envelope; отмена Task является новым действием; irreversible provider outcome не переписывается | UX для компенсирующего email учитывается отдельно в сценарии E |
| **Итого §31.8** | **6 / 6** |  |  |  |

## Матрица дополнительных сценариев A–G

| Сценарий §31.9 | Балл | Статус | Фактическое доказательство | Остаток |
|---|---:|---|---|---|
| A. Срок вместе с evidence на точный источник/версию; без evidence — unverified | 1 | CODE/CONTRACT PASS | C01 извлекает deadline и exact text-range evidence из corpus bytes; C07 хранит date/time/fixed offset без молчаливого усечения; manual review остаётся обязательным | Требуется новый runtime на `a09`; live provider остаётся отдельным gate |
| B. `create-internal-task=AUTO`, `send-external-message=CONFIRM` | 1 | CODE PASS | a07 разрешает low-risk `task.internal.create` через SERVER_POLICY без фиктивного approval; тот же product-like сценарий требует HUMAN_APPROVAL для external action и проверяет UNKNOWN reconciliation | Live send намеренно не выполняется в CI |
| C. Изменённый payload инвалидирует старое approval | 1 | CODE PASS | Approval привязан к action/revision/envelope/payload hash; негативные T2 tests блокируют подмену до эффекта | Требуется только общий Wave3 runtime gate |
| D. Reversible Task отменяется отдельным audited action | 1 | CODE PASS | `task.internal.cancel` имеет отдельные action, permission/approval, receipt и audit; исходная история сохраняется | Нет остатка в границе synthetic pilot |
| E. Для отправленного email UI предлагает новый compensating follow-up | 1 | CODE PASS | Исходный APPLIED email неизменяем; API/UI создают отдельный FROZEN corrective draft с новым CONFIRM, exact source/outcome/mailbox/project/evidence pins и fail-closed recheck | Live send намеренно не выполняется до отдельной provider acceptance |
| F. Source остаётся у клиента; запрещённая копия не создаётся | 1 | CODE + RUNTIME PASS | a05 связывает SourceVersion/Evidence/Materialization, staging шифрует представление, payload содержит только `staging_id`; a08 добавляет service retention purge и DB/lease fence от дублей | Product enable и live-provider policy остаются отдельными решениями |
| G. Недоступный или устаревший provider source явно маркируется | 1 | CODE PASS | Fail-closed resolver и EvidencePanel показывают unavailable/stale без выдачи fragment; late reply не возвращает старый проект | Live provider outage ещё не проверен |
| **Итого §31.9** | **7 / 7** |  |  |  |

## Реализованная последовательность схемы

| Ревизия | Назначение | Состояние в кандидате |
|---|---|---|
| `a54f001c0a03` | Durable mailbox identity и credential generations | INTEGRATED |
| `a54f001c0a04` | Mailbox-scoped dedup и cutover constraints | INTEGRATED |
| `a54f001c0a05` | SourceVersion-bound materialization lifecycle | INTEGRATED |
| `a54f001c0a06` | Provider action, approval, outbox, attempt и outcome schema | INTEGRATED |
| `a54f001c0a07` | Явный HUMAN_APPROVAL/SERVER_POLICY authorization origin | INTEGRATED |
| `a54f001c0a08` | Service retention purge и DB/lease fence local materialization | INTEGRATED |
| `a54f001c0a09` | Точное время и fixed-offset timezone DeadlineClaim | CURRENT HEAD |

Все runtime/readiness pins проверенного HEAD ожидают `a54f001c0a09`. Миграция
`a09` последовательна после `a08`; новой merge head не создаётся.

## Фактическое покрытие Wave2 и Wave3

Профильные доказательства: [mailbox identity](v54-mailbox-identity-implementation.md),
[rollout controls](v54-mailbox-rollout-controls.md),
[Evidence API/UI](v54-evidence-product-api-ui.md),
[local upload staging](v54-local-upload-staging.md),
[Gmail attachment wiring](v54-gmail-a05-wiring.md),
[provider runtime](v54-provider-action-runtime.md),
[autonomy authorization](v54-autonomy-authorization.md),
[product-like acceptance](v54-product-acceptance.md),
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
- product-like acceptance: default-off/test-DB-only HTTP path проверяет полный
  A/B цикл, раздельные HUMAN_APPROVAL/SERVER_POLICY origins, UNKNOWN lookup и
  отсутствие повторного provider effect;
- C01 content bridge: synthetic-only extractor читает реальные bytes TXT/MD,
  создаёт exact text-range evidence и передаёт извлечённые project, contract и
  deadline в существующие writers без oracle в extractor;
- C07 deadline precision: `due_time` и fixed-offset timezone сохраняются без
  усечения, а date-only Task sealing точного времени завершается fail closed;
- S02/S07/S08 acceptance: PostgreSQL multi-mailbox isolation и точные
  process-fault boundaries включены в runtime protocol;
- browser E2E: пять synthetic Playwright-сценариев на реальном `App` покрывают
  readable/manual-review evidence, stale/denied fail-closed, late-reply без
  возврата к старому проекту, attachment import UI и local-upload/AI-policy
  маршруты. Это не live-provider и не browser→PostgreSQL→worker доказательство.

Последний локальный объединённый adversarial прогон перед a07 зафиксировал
`1087 passed, 15 skipped`; профильный a07 regression — `87 passed, 1 PostgreSQL
skip`; локальный Wave3 CI contract — `26 passed`.

Базовый candidate `f869319` прошёл обязательные GitHub Actions:

- `v5.4 runtime`, run `33865331595`, `5m40s` — PASS; artifact SHA-256
  `c9f076ab9fddc652904e2a10d918e64d9d459a93aaa389fb8e825572ba8b4575`;
- durable queue, run `33865331624` — PASS;
- общий CI, run `33865331644` — PASS;
- Docker smoke, run `33865331681` — PASS.

Его runtime protocol подтверждает `result=PASS`, `cleanup=PASS`, head
`a54f001c0a08`, backend `1123 passed / 15 skipped`, PostgreSQL A/B/C
`293 passed / 0 failed` и `process_reclaim=PASS`.

Новый `f0c0e26` добавляет исполняемые `C01/C07/S02/S07/S08` и миграцию `a09`.
Эти изменения ещё не проходили GitHub runtime на точном новом commit, поэтому
исторические зелёные runs остаются доказательством базы, но не нового delta.

## Оставшиеся обязательные gate

### 1. Повторный runtime на `f0c0e26`

Нужно повторно выполнить на точном новом кандидате:

- `v54-pilot-runtime.yml` — upgrade чистой PostgreSQL до `a54f001c0a09`, A/B/C,
  `C01/C07/S02`, точные `S07/S08`, Authority/Context/Source CAS и cleanup;
- `durable-queue.yml` — два API, два worker, scheduler, retry/dead-letter/
  redrive/cancel, backup/restore и cleanup.
- общий CI и Docker smoke.

PASS принимается только если safe protocol показывает head `a54f001c0a09`,
`result=PASS`, `cleanup=PASS`, executed cases `C01/C07/S02/S07/S08` и remaining
gaps только `P04/S10`.

### 2. `C01/C07/S02/S07/S08` — закрыты кодом, ожидают runtime

Новые contract-тесты и harness закрывают прежние gaps. Их нельзя возвращать в
список незакрытого функционального scope, но нельзя объявлять runtime PASS до
исполнения на Linux/PostgreSQL.

### 3. Email compensation — закрыто кодом

Сценарий E реализован как безопасный продуктовый результат:

1. отправленное письмо помечается `IRREVERSIBLE` и не предлагает фиктивный undo;
2. UI предлагает создать новое corrective follow-up action;
3. follow-up получает новый immutable payload, новый approval/policy decision,
   отдельный idempotency key, receipt и audit;
4. исходный email/outcome не переписывается;
5. synthetic browser/API tests подтверждают поведение без реальной отправки.

### 4. Два P1 blocker staging — закрыты кодом и базовым runtime

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

PostgreSQL run на `a08` и durable queue workflow прошли. Runtime composition
остаётся default-off/fail-closed до явного product enable. Новый `a09` не меняет
staging, но итоговый release gate всё равно требует общий повторный CI на одной
зафиксированной head.

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
| 13 критериев MVP5 | **PASS, 13/13 CODE/CONTRACT** | `C01/C07/S02/S07/S08` интегрированы; runtime является отдельным gate |
| Единственная Alembic head | CODE PASS | Сохранить линейную head `a09`; подтвердить чистым upgrade |
| Wave2 Linux/PostgreSQL runtime | PASS | Уже подтверждён на `f721634` тремя зелёными runs |
| Базовый `f869319` Wave3 runtime | PASS | Runtime `33865331595`, durable `33865331624`, CI `33865331644`, Docker `33865331681` |
| Новый `f0c0e26` Wave3 runtime | CONDITIONAL | Повторить четыре workflow на head `a09` |
| `C01/C07/S02/S07/S08` | CODE/CONTRACT PASS | Новый protocol должен выполнить эти cases без gaps |
| Staging security runtime | BASE PASS | a08 PostgreSQL и durable runtime пройдены; итоговый candidate ждёт общий rerun |
| Email compensation | CODE PASS | Новый corrective follow-up action и UI/API tests готовы; live send не выполнялся |
| `P04` finance | OUT OF MVP5 / OWNER DECISION | Отдельный scope, если владелец включает финансовый execution |
| `S10` live provider | SYNTHETIC PASS / LIVE NOT RUN | Изолированная учётная запись, no duplicate effect и reconciliation |
| Production enable | BLOCKED | Новый runtime PASS, live-provider gate и отдельное разрешение владельца |

## Следующий безопасный порядок

1. Выполнить runtime, durable queue, общий CI и Docker smoke на `f0c0e26` или его
   docs-only потомке.
2. Проверить head `a09`, executed `C01/C07/S02/S07/S08`, remaining только
   `P04/S10` и cleanup PASS.
3. Провести отдельный live-provider sandbox acceptance для `S10`.
4. Не выполнять merge или production deploy до отдельного решения владельца.

## Итоговый статус

Кандидат `f0c0e26` реализует **100% code/contract scope 13 явных критериев
MVP5**. Прежние gaps `C01/C07/S02/S07/S08` закрыты кодом. Runtime остаётся
CONDITIONAL только потому, что новый delta и head `a54f001c0a09` ещё не прошли
повторный CI. Functional remaining gaps — только `P04` вне MVP5 и live-provider
`S10`. Расширенная Company Memory относится к `1.0+` и не должна задерживать
закрытие MVP5.
