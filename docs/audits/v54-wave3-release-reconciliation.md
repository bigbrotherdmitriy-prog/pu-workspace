# PU Workspace v5.4 Wave 3 — сверка релизного gate

Дата: 2026-09-04

Проверяемый кандидат: `f869319e226d0563d9c95eec408adcf716ed7e9f`

Runtime run: `33865331595`

Runtime artifact SHA-256:
`c9f076ab9fddc652904e2a10d918e64d9d459a93aaa389fb8e825572ba8b4575`

Текущая единственная Alembic head: `a54f001c0a08`

## Решение

**Wave 3 PostgreSQL runtime: PASS. MVP5 contract coverage: 100%. Полная MVP5
приёмка: CONDITIONAL. Live-provider и коммерческая выдача: NOT READY.**

Run `33865331595` снял прежний общий blocker «нет PostgreSQL runtime на Wave 3»:

- backend: `1123 passed`, `15 skipped`;
- PostgreSQL-поднабор: `293 passed`, `0 failed`;
- `process_reclaim`: PASS;
- cleanup: PASS;
- схема после миграций: `a54f001c0a08`.

Длительность runtime run: `5m40s`. Сопутствующие обязательные workflow на том
же кандидате также завершились успешно:

- durable queue, run `33865331624` — PASS;
- общий CI, run `33865331644` — PASS;
- Docker smoke, run `33865331681` — PASS.

Этот результат подтверждает выполнение реально запущенных фаз. Он не превращает
перечисленные самим протоколом gaps `C01`, `C07`, `P04`, `S02`, `S07`, `S08` и
`S10` в PASS. Ниже они классифицированы по фактическим границам ТЗ, без
расширения MVP5.

## Основание классификации

В исходном DOCX §31.8 определяет MVP5 Pilot Ready шестью обязательными
свойствами: evidence-backed end-to-end Communication-to-Action, пилотный
Context Graph, CONFIRM, Action Ledger, idempotency/deduplication и базовая
классификация reversible/compensatable/irreversible. §31.9 добавляет сценарии
A–G. §31.8 прямо относит расширенную Company Memory, дополнительные Agents,
richer Context Graph и enterprise policy automation к `1.0+`.

Термин `100% contract coverage` означает, что все 13 явных критериев имеют
интегрированные контракты и автоматические проверки. Это не равнозначно полной
приёмке: product-like тест создаёт Source/Evidence/Deadline через сервисные
методы и не доказывает извлечение этих фактов из содержимого письма и вложения.

## Обязательное для закрытия MVP5

| ID | Статус | Почему остаётся | Условие закрытия |
|---|---|---|---|
| `C01` | **BLOCKER — product E2E** | §31.8 и сценарий A требуют цепочку от входящего содержимого до evidence, срока, задачи и черновика. Текущий synthetic acceptance регистрирует Source/Evidence/Deadline программно; runtime-протокол прямо сообщает, что content extraction и corpus due-date input не подключены | Подключить синтетические письмо и вложение к реальному extraction/context pipeline и доказать один HTTP/worker/PostgreSQL сценарий без ручного seed структурированного claim |
| `S07` | **BLOCKER — fault evidence** | Общий `process_reclaim=PASS` не является точной fault injection после commit pending intent и до enqueue | Воспроизвести process kill на указанной границе и доказать восстановление одного intent, одной Task и одного receipt |
| `S08` | **BLOCKER — fault evidence** | Проверен reclaim, но не точная граница после business commit и до job completion | Воспроизвести kill на указанной границе и доказать отсутствие повторного business effect после lease recovery |

`S07` и `S08` могут закрыться тестами без изменения продуктовой логики, если
существующая реализация выдержит fault injection. `C01` является именно пробелом
сквозной композиции/доказательства, а не только отсутствующим отчётом.

## Требует решения владельца, но не является безусловным MVP5 blocker

| ID | Классификация | Решение владельца |
|---|---|---|
| `C07` | точность срока | Выбрать: добавить timestamp/timezone в общий DeadlineClaim либо явно ограничить MVP5 календарной датой. До решения нельзя молча отбрасывать `18:30 UTC+03:00` |
| `P04` | будущий финансовый контур | Подтвердить отдельный scope пользовательского события оплаты и интеграции с ДДС. §31.8/31.9 не требует финансового execution; письмо со счётом само по себе не доказывает оплату |
| `S02` | multi-mailbox rollout | Для single-mailbox пилота не блокирует §31.8/31.9. Для нескольких mailbox/account владелец должен либо ограничить pilot cohort одним mailbox, либо потребовать полный legacy identity cutover до пилота |
| каноническая версия | release metadata | Имя файла содержит `v5_4`, титул DOCX — `5.1`. Перед внешней передачей выбрать одно обозначение |

Если владелец включает timestamp, finance execution или multi-mailbox в
утверждённый MVP5 scope, соответствующий пункт становится обязательным blocker
и должен получить отдельные критерии приёмки.

## Live-provider gate

| ID / контур | Статус | Что ещё требуется |
|---|---|---|
| `S10` UNKNOWN/reconciliation | **SYNTHETIC PASS / LIVE NOT RUN** | Изолированная provider sandbox, timeout-after-effect, lookup/reconciliation и доказательство одного внешнего эффекта без blind retry |
| Gmail/Google provider action | **LIVE NOT RUN** | Тестовая учётная запись, exact mailbox/credential generation, CONFIRM конкретной версии, receipt и audit |
| `S02` multi-mailbox | **LIVE NOT RUN** | Два тестовых mailbox с одинаковым provider message id и независимыми origins, если multi-mailbox включён в pilot cohort |
| Provider outage/stale source | **SYNTHETIC PASS / LIVE NOT RUN** | Управляемая недоступность/устаревание provider source и fail-closed UI/API |

Live-provider acceptance не входит в процент contract coverage. Она обязательна
перед заявлением о готовности реальной интеграции и перед production enable.

## Требования 1.0+ и будущих версий

Эти пункты не являются остатком MVP5 и не должны снижать его процент:

- расширенная Company Memory;
- дополнительные специализированные AI Agents;
- richer enterprise Context Graph;
- enterprise policy automation;
- дополнительные provider adapters сверх пилотного пути;
- полноценный graph engine;
- автономное выполнение high-risk, финансовых, юридических, платёжных и
  destructive actions.

## Коммерческий и юридический gate

Runtime PASS не закрывает коммерческую выдачу. По текущему SBOM/legal-аудиту
реально остаются:

### Требуется решение владельца

- правообладатель и год в корневом `LICENSE`;
- модель лицензирования/сделки и точный состав передачи;
- каноническое обозначение версии релиза;
- подтверждение происхождения пяти PU PWA icons и цепочки прав на собственный
  код/материалы;
- отдельное разрешение на merge, production enable и deploy.

### Требуется документ или работа release manager

- воспроизводимый Python transitive lock с hashes;
- digest-pinned container inventory и layer/apt SBOM;
- package-specific LICENSE/COPYING/NOTICE bundle;
- release archive/manifest/checksum, сформированные только после фиксации
  финального release SHA.

### Требуется профильный юрист

- цепочка исключительных прав и разрешённые способы использования;
- `licenseConcluded` для компонентов, обязанности по `psycopg`/LGPL и
  контейнерным слоям;
- финальный `LICENSE`/`NOTICE` и модель договора;
- режим ПДн, AI providers/subprocessors, retention и data residency для
  использования реальных данных;
- пакет Реестра российского ПО, если владелец решает подавать заявление.

## Итоговый gate

| Gate | Статус после run `33865331595` | Комментарий |
|---|---|---|
| 13 критериев MVP5 — contract/code coverage | **PASS, 13/13** | Все критерии имеют интегрированные контракты и тесты |
| Alembic `a54f001c0a08` | **PASS** | Единственная head подтверждена runtime |
| Wave 3 PostgreSQL runtime | **PASS** | 1123/15 backend, 293/0 PostgreSQL, reclaim и cleanup PASS |
| Durable queue / Docker smoke / общий CI | **PASS** | Runs `33865331624`, `33865331681`, `33865331644` |
| Полный product E2E сценарий A | **BLOCKED (`C01`)** | Реальная extraction-to-claim композиция не доказана |
| Точные crash boundaries | **BLOCKED (`S07`, `S08`)** | Общий reclaim не заменяет два конкретных fault-сценария |
| Live provider | **NOT RUN** | `S10` и live mailbox/provider acceptance остаются отдельными |
| Production enable | **BLOCKED** | Нужны закрытые MVP5 blockers, live-provider gate и решение владельца |
| Коммерческая выдача | **BLOCKED** | Нужны owner/legal/release artifacts, перечисленные выше |

## Следующий безопасный порядок

1. Закрыть `C01` реальной product-композицией на синтетическом содержимом.
2. Выполнить точные process-fault сценарии `S07` и `S08` на PostgreSQL.
3. Если pilot cohort включает несколько ящиков, закрыть `S02`; иначе письменно
   зафиксировать single-mailbox ограничение.
4. Провести live-provider sandbox acceptance для `S10` и Gmail/provider action.
5. Параллельно собрать release/legal документы; не смешивать их с технической
   MVP5-приёмкой.
6. Не выполнять merge или production deploy без отдельного решения владельца.
