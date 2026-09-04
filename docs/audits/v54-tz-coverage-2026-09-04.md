# PU Workspace v5.4 — каноническое покрытие ТЗ и roadmap после MVP5

Дата актуализации: 2026-09-04

Источник требований: `PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`

SHA-256 источника: `af7bfde75715345e4f32b9d7ca057812cdba7b8d8e0b6a1b105dfe20fc0d5df3`

Проверенный кандидат: `8ccc194bc834328e51a73225981f74d81775789a`

Единственная Alembic head: `a54f001c0a09`

Этот документ является канонической точкой планирования по полному ТЗ. Более
ранние аудиты сохраняются как доказательства отдельных изменений, но их статусы
`CONDITIONAL` не переопределяют более поздний runtime-результат на точном SHA.

## Решение

**Технический MVP5 Pilot Ready: PASS, 13 из 13 критериев, PostgreSQL runtime
PASS.** Изолированный GitHub Actions run
[`33872553514`](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33872553514)
проверил точный commit `8ccc194`, чистый upgrade до `a54f001c0a09`, полный
backend, PostgreSQL A/B/C integration, corpus, gzip regression и реальные
process-fault границы. Safe protocol зафиксировал `result=PASS`, `cleanup=PASS`,
отсутствие публикации raw output и исполнение `C01`, `C07`, `P02`, `P06`,
`S02`, `S06`, `S07`, `S08`, `S09`.

Этот PASS не означает:

- live-provider readiness: `S10` проверен только синтетическим adapter;
- готовность финансового контура: `P04` сознательно находится вне MVP5;
- production enable: новые контуры остаются default-off до отдельной композиции,
  canary и решения владельца;
- коммерческую или юридическую готовность: права, лицензирование, SBOM/NOTICE,
  ПДн и документы передачи имеют независимые gates;
- завершение всего Product Scope: MVP3, MVP4, MVP6 и 1.0+ остаются roadmap.

## Правила статусов

| Статус | Значение |
|---|---|
| `RUNTIME PASS` | Код интегрирован в указанный SHA и обязательный сценарий исполнен на изолированной PostgreSQL/Linux среде |
| `CONTRACT PASS` | Контракт и автоматические тесты есть, но live-provider или production execution не выполнялся |
| `EXISTING / ACCEPTANCE REQUIRED` | Рабочая реализация существует, но полная приёмка требования ТЗ на текущем release evidence не доказана |
| `BACKLOG` | Требование Product Scope ещё должно быть реализовано отдельным вертикальным срезом |
| `OWNER DECISION` | Реализация зависит от продуктового, коммерческого или риск-решения владельца |
| `LEGAL REVIEW` | Нужны документы или заключение профильного юриста; кодом закрыть нельзя |
| `LIVE GATE` | Нужен изолированный тест с реальным провайдером и тестовой учётной записью |
| `PRODUCTION GATE` | Нужны rollout, эксплуатационная конфигурация и наблюдение без изменения требований продукта |
| `NON-GOAL` | Не включать в текущую волну либо не реализовывать без нового утверждённого scope |

Процент не смешивает эти категории. `100% MVP5` относится только к 13 явно
зафиксированным критериям MVP5. Для полного Product Scope используется
поштучный backlog и gate readiness, а не вводящий в заблуждение общий процент.

## Доказательство runtime на точном кандидате

| Проверка | Фактический результат |
|---|---|
| Commit | `8ccc194bc834328e51a73225981f74d81775789a` |
| Schema | одна head `a54f001c0a09` |
| Migration | exit `0`, чистая PostgreSQL |
| Полный backend | `1132 passed`, `16 skipped`, exit `0` |
| PostgreSQL A/B/C integration | `301 passed`, `0 skipped`, exit `0` |
| Corpus | structural `PASS` |
| Process reclaim | `PASS`, один Task, receipt, projection и success audit |
| `S07` commit-before-enqueue | process kill и восстановление одного intent/job/Task/receipt — `PASS` |
| `S08` pre-commit | незакоммиченные Task/receipt равны нулю — `PASS` |
| `S08` post-business-commit | lease reclaim и receipt replay без второго эффекта — `PASS` |
| Безопасность протокола | raw output не опубликован; точная allowlist schema |
| Cleanup | тестовая схема удалена, `cleanup=PASS` |

Смежные workflows на том же SHA: Docker smoke `33872553529`, общий CI
`33872553588` и durable queue recovery `33872553425` — PASS. Они являются
release evidence для кандидата, но не разрешением на merge или deploy.

## MVP5 — 13 из 13

### Шесть признаков Pilot Ready из раздела 31.8

| ID | Критерий | Статус | Доказательство и граница |
|---|---|---|---|
| M5-01 | Evidence-backed end-to-end Communication-to-Action | `RUNTIME PASS` | `C01` читает синтетические corpus bytes, создаёт exact Evidence и проходит Context → DeadlineClaim → Trust → Task → receipt; production ingress не включён |
| M5-02 | Context Graph пилотного сценария | `RUNTIME PASS` | Hypothesis/confirm/correct, CAS, история, защита от late analysis и receipt projection исполнены на PostgreSQL |
| M5-03 | CONFIRM для критических действий | `RUNTIME PASS` | Approval привязан к точной revision/envelope/payload; Authority перепроверяется перед T2; service worker и global admin не обходят правила |
| M5-04 | Action Ledger | `RUNTIME PASS` | Append-oriented source → analysis → proposal → decision → execution → outcome/audit; safe protocol подтвердил один success audit |
| M5-05 | Idempotency и deduplication | `RUNTIME PASS` | Mailbox-scoped dedup, S02, S06, S07/S08, один Task/receipt/projection, UNKNOWN → lookup без blind retry |
| M5-06 | Reversible/compensatable/irreversible | `RUNTIME PASS` | Класс запечатан в action; Task cancel — отдельное действие; email outcome неизменяем и допускает отдельный corrective follow-up |

### Семь дополнительных сценариев из раздела 31.9

| ID | Сценарий | Статус | Доказательство и граница |
|---|---|---|---|
| M5-07 | A. Договорный срок с evidence; без evidence — unverified | `RUNTIME PASS` | `C01/C07`: source/version pin, точные координаты, date/time/fixed offset; low confidence требует human review |
| M5-08 | B. Internal task AUTO, external message CONFIRM | `RUNTIME PASS` | Узкая SERVER_POLICY только для low-risk `task.internal.create`; external/finance/legal/destructive AUTO запрещены |
| M5-09 | C. Изменение payload инвалидирует approval | `RUNTIME PASS` | Hash/revision/envelope binding проверяется до T2 |
| M5-10 | D. Reversible Task отменяется отдельным audited action | `RUNTIME PASS` | Отдельные action, permission/approval, receipt и audit без переписывания истории |
| M5-11 | E. Email не имеет фиктивного undo, доступен corrective follow-up | `RUNTIME PASS` | Отдельный FROZEN corrective draft с новым CONFIRM; реальная отправка не выполнялась |
| M5-12 | F. Source может оставаться у клиента; запрещённая копия не создаётся | `RUNTIME PASS` | Materialization/SourceVersion/Evidence policy, opaque `staging_id`, AES-256-GCM, retention purge и lease fence |
| M5-13 | G. Недоступный/устаревший source явно маркируется | `RUNTIME PASS` | Fail-closed resolver и Evidence UI не возвращают fragment; live provider outage относится к `S10` |

## Покрытие остальных разделов ТЗ

| Разделы ТЗ | Область | Текущий статус | Следующий контролируемый результат |
|---|---|---|---|
| 1–3 | Project Context и AI Secretary | `EXISTING / ACCEPTANCE REQUIRED` | Live ingress и полная пользовательская E2E-приёмка поверх общего ядра |
| 4–6, 24–25 | MVP-1/MVP-2 пути | MVP5 synthetic/runtime `PASS`; live adapters частично не проверены | Реальные Drive/Gmail/Tasks/Calendar test accounts, без production данных |
| 7–8 | Роли, права и центральные сущности | Authority v5.4 `RUNTIME PASS`; полный продуктовый RBAC требует матрицы | Ролевая conformance suite по каждому API и adapter capability |
| 9–10, 12–13, 18 | Snapshot, Drive, native files, массовые операции, rollback | `EXISTING / ACCEPTANCE REQUIRED` | Live V1 acceptance: nested/multi-folder, revision invalidation, conflict и rollback |
| 11, 26 | Gmail/Tasks/Calendar/Telegram adapters | Mailbox identity и synthetic actions `RUNTIME PASS`; live channel gate открыт | `S10` и по одному live sandbox E2E для разрешённых adapters |
| 14 | PDF/DOCX/XLSX/images/OCR | Расширенный OCR и manual review существуют; production OCR выключен | Corpus benchmark, encrypted-temp proof и staged production rollout |
| 15–16 | AI rules, quality, DLP | Evidence/confidence/approval контракты есть; org-wide policy incomplete | Версионирование prompts, DLP policy, local-only и provider conformance |
| 17 | Полный интерфейс | Pilot Evidence/Context UX и storage picker покрыты частично | Единые карточки source/evidence/action/ledger, accessibility и browser E2E |
| 19–23 | Audit, reliability, security, API, testing | Durable queue и MVP5 runtime `PASS`; production operations отдельно | SLO, DR, performance 1k/10k, pen-test, API/error freeze |
| 27 | ГПР, бюджет, ДДС, закупки, поставки, акты | `BACKLOG`; `P04` вне MVP5 | Отдельный финансовый vertical slice после owner/legal решений |
| 28–30, 32–40 | Артефакты, scope, V1 backlog, stop conditions | Значительная часть документации и тестов есть, но полный release dossier не закрыт | Трассируемая матрица требований, актуальные ADR и финальные acceptance logs |
| Provider-Agnostic Architecture | Storage/mailbox/AI seams заложены; Google/Yandex частично есть | `CONTRACT PASS` | Capability conformance и adapters для выбранных рынком провайдеров |
| Context → Action → Human Control | Узкий пилот | `RUNTIME PASS` | Расширение типов, каналов и пользовательских сценариев без обхода Trust Loop |
| Federated Source/Evidence/Autonomy/Compensation | Узкий пилот | `RUNTIME PASS` | Production composition, live source и enterprise policy |
| Company Memory | Целевая подтверждённая память | `BACKLOG 1.0+` | Provenance-first memory lifecycle; не обучение на каждой правке |

## Открытые границы, не являющиеся дефектом MVP5

### P04 — финансы

Письмо со счётом не является доказательством оплаты. Если владелец открывает
финансовый контур, факт оплаты создаётся только отдельным пользовательским
подтверждением с evidence, ролью, этапом и audit. Автоматическое изменение ДДС,
платёж или юридическое обязательство по одному AI-выводу запрещены.

### S10 — live provider

Synthetic provider доказал `UNKNOWN`, lookup/reconciliation и отсутствие blind
retry. Не выполнен сетевой timeout-after-effect на изолированной тестовой
учётной записи с подтверждением ровно одного внешнего эффекта. До этого нельзя
заявлять live-provider readiness.

### Production enable

Наличие кода не включает local upload, Gmail attachment processing, fragment
store, AUTO или provider action. Production composition обязана явно установить
scoped authority, KEK/residency/retention, rollout flags, мониторинг и rollback.

### Коммерческая выдача

Технический PASS не подтверждает права, модель сделки, ПДн, лицензионные
обязанности, полноту контейнерного SBOM или готовность Реестра российского ПО.

## Atomic backlog

Каждый пункт ниже должен выполняться отдельным небольшим изменением с regression
test и проверяемым результатом. `Depends` ссылается на ID этого документа.
Приоритет: `P0` — блокирует следующий gate, `P1` — следующий продуктовый этап,
`P2` — плановый 1.0+, `P3` — только после отдельного решения. Риск `H/M/L`
определяет обязательность review и fault/security tests.

Всего зафиксировано 86 атомарных пунктов: 7 owner decisions, 16 live/release
gates, 9 production rollout, 11 MVP3, 10 MVP4, 11 MVP6, 15 для 1.0+ и 7
legal/release. По приоритету: 28 P0, 40 P1, 17 P2 и 1 P3. Это размер
оставшегося Product Scope, а не процент незавершённости уже принятого MVP5.

### A. Решения владельца

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| OWN-01 | OWNER | Утвердить каноническую версию продукта: `v5.4` против титула `5.1` | — | P0 / L | Один version string используется в ТЗ, release manifest, UI и legal docs; история не переписывается |
| OWN-02 | OWNER | Зафиксировать, остаётся ли `P04` вне ближайшего релиза | — | P0 / H | Подписанное scope decision: out-of-scope либо отдельный finance epic с бюджетом и владельцем |
| OWN-03 | OWNER | Выбрать pilot/live providers и тестовые учётные записи | — | P0 / M | Перечень provider/capability/cohort, ответственные, лимиты и запрет production credentials |
| OWN-04 | OWNER | Утвердить разрешённые autonomy actions | — | P0 / H | Матрица action type × risk × role × mode; high-risk default CONFIRM/DENY; versioned approval |
| OWN-05 | OWNER | Утвердить cloud/private/on-prem editions и data residency | — | P0 / H | Для каждой редакции определены storage, AI egress, регионы, ключи, backup и ответственность |
| OWN-06 | OWNER | Утвердить RPO/RTO, retention и incident owners | — | P0 / H | Численные RPO/RTO/retention, on-call и критерии восстановления приняты владельцем |
| OWN-07 | OWNER | Утвердить модель сделки и состав коммерческой передачи | — | P0 / H | Выбрана лицензия/отчуждение, состав artifacts, цена/условия и подписанты переданы юристу |

### B. Live и release gates после MVP5

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| GATE-01 | LIVE | Закрыть `S10` на изолированном live provider sandbox | OWN-03 | P0 / H | timeout-after-effect; lookup/reconciliation; один внешний effect; safe audit; test account удалён/отозван по процедуре |
| GATE-02 | LIVE | Выполнить полный Google Drive V1 acceptance | OWN-03 | P0 / H | OAuth test account; папка любой глубины; multi-folder; snapshot без copy; standard naming; native revision invalidation; conflict; idempotent rename/move; rollback |
| GATE-03 | LIVE | Выполнить Gmail → Context → draft → action acceptance | OWN-03, GATE-01 | P0 / H | Mailbox-scoped origin; duplicate delivery; correction; low confidence review; reply/Task/Calendar ровно один раз |
| GATE-04 | LIVE | Проверить Яндекс 360 storage path | OWN-03 | P1 / M | Unicode/deep path, pagination, capability denial, stale version и повтор без дубля на test account |
| GATE-05 | PRODUCT | Провести OCR benchmark на обезличенном корпусе | — | P1 / M | ≥95% страниц без технического сбоя; page confidence; field precision/recall; evidence coordinates; low confidence всегда review |
| GATE-06 | PRODUCT | Проверить 1 000/10 000 metadata performance | — | P1 / M | 1k ≤30 s, 10k ≤5 min либо утверждённое отклонение; повтор обрабатывает только delta; результаты воспроизводимы |
| GATE-07 | PRODUCTION | Выполнить install/backup/restore/rollback drill | OWN-05, OWN-06 | P0 / H | Чистая установка, upgrade, backup, restore в отдельную БД, проверка сущностей, измеренные RPO/RTO, cleanup |
| GATE-08 | SECURITY | Провести независимый tenant/ACL/secret/PII review | OWN-05 | P0 / H | Нет cross-tenant доступа, raw content в logs/jobs/artifacts, обхода approval/authority; findings закрыты или приняты |
| GATE-09 | PRODUCT | Зафиксировать versioned API и error catalog | — | P1 / M | OpenAPI/DTO/error codes/idempotency/correlation semantics versioned; compatibility tests; no provider-specific core fields |
| GATE-10 | PRODUCTION | Ввести SLO и безопасную наблюдаемость | OWN-06 | P1 / M | Queue age/depth, worker heartbeat, provider health, latency/error budgets; alerts без PII/content/DSN |
| GATE-11 | PRODUCTION | Проверить production-like graceful shutdown/recovery | GATE-07 | P0 / H | API/worker/scheduler restart, lease recovery, dead-letter/redrive/cancel, no double effect и no lost job |
| GATE-12 | PRODUCT | Закрыть доступность и полный browser E2E ключевых экранов | GATE-02, GATE-03 | P1 / M | Desktop/mobile, keyboard/a11y, progress, stale reply, evidence, approval и rollback проходят в Chromium без mock-only подмены core |
| GATE-13 | PRODUCT | Принять массовую работу с деревом и документами | GATE-02, GATE-06 | P1 / M | Server pagination/virtualization, bulk selection, saved filters, per-object progress/error/conflict и incremental rerun проходят на 10k corpus |
| GATE-14 | SECURITY | Проверить lifecycle OAuth/API credentials и ключей | OWN-05, GATE-08 | P0 / H | Field encryption, least scopes, generation/revoke, rotation, backup exclusion и redacted audit подтверждены fault/security tests |
| GATE-15 | PRODUCT | Ввести prompt/model/version quality governance | GATE-05 | P1 / M | Версионированные prompts/classifiers, offline eval, structured-output failure metrics, rollback и per-tenant AI policy |
| GATE-16 | PRODUCT | Принять local-only и AI-outage режим | OWN-05, M6-05 | P1 / H | При запрете/недоступности внешнего AI source не теряется, ручная работа доступна, content не выходит наружу, audit объясняет fallback |

### C. Production composition и controlled rollout

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| PROD-01 | PRODUCTION | Установить production composition для encrypted staging | OWN-05, OWN-06, GATE-08 | P0 / H | Отдельный KEK, exact scopes/residency/retention, private shared storage, missing key fail-closed, rotation drill |
| PROD-02 | PRODUCTION | Подключить production FragmentStore | PROD-01, GATE-08 | P0 / H | Только authorized historical/current pins; revoked/purged/stale одинаково unavailable; no cache leakage |
| PROD-03 | PRODUCTION | Включить mailbox shadow compare на allowlist cohort | GATE-03, GATE-08 | P0 / H | Flags default false; mismatch report PII-free; rollback выключает cohort без потери origin history |
| PROD-04 | PRODUCTION | Выполнить staged mailbox cutover | PROD-03, GATE-11 | P0 / H | shadow → pilot write → primary read → actions; CAS generation/binding; legacy fallback запрещён для cohort |
| PROD-05 | PRODUCTION | Включить local upload canary | PROD-01, GATE-05, GATE-11 | P0 / H | Размер/MIME policy, encrypted staging, single BackgroundJob, terminal cleanup/retention recovery и operator rollback |
| PROD-06 | PRODUCTION | Включить CONFIRM external provider canary | GATE-01, GATE-08, GATE-11 | P0 / H | Только allowlisted action/provider/cohort; exact approval; UNKNOWN reconciliation; kill switch; no AUTO external |
| PROD-07 | PRODUCTION | Провести limited pilot observation | PROD-02..PROD-06, PROD-09 | P0 / H | Утверждённое окно, SLO без нарушений, incidents разобраны, data retention выполнен, решение go/no-go записано |
| PROD-08 | PRODUCTION | Зафиксировать release candidate и deployment approval | PROD-07, LEG-01..LEG-05 | P0 / H | Финальный SHA, зеленые gates, подписанный change/rollback plan; отдельное явное разрешение на deploy |
| PROD-09 | PRODUCTION | Включить OCR canary через durable processing | PROD-01, GATE-05, GATE-11 | P1 / H | Local Tesseract baseline; page evidence/confidence; external vision только через AIProviderAdapter; low confidence блокирует legal/financial actions |

### D. MVP3 — управленческий контур

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| M3-01 | PRODUCT | Завершить lifecycle обязательства | GATE-09 | P1 / M | Obligation имеет evidence, owner, due, status, version/CAS, history и links к Project/Contract/Source |
| M3-02 | PRODUCT | Унифицировать внутренние и внешние задачи | M3-01 | P1 / M | Task state mapping, provider external ID, idempotent sync, manual correction и audit без потери origin |
| M3-03 | PRODUCT | Реализовать deadline/escalation policy | M3-01, OWN-04 | P1 / M | Date/time/timezone, reminders, overdue escalation, quiet hours; неоднозначный срок требует человека |
| M3-04 | PRODUCT | Завершить Risk и Decision lifecycle | M3-01 | P1 / M | Evidence-backed risk/decision, severity, owner, mitigation, status/history и relation к obligation/task/change |
| M3-05 | PRODUCT | Добавить meeting/action extraction | M3-02, M3-04 | P1 / M | Протокол/сообщение → proposed decisions/tasks; confirmation before durable links/actions |
| M3-06 | PRODUCT | Собрать dashboard Требует внимания | M3-02..M3-05 | P1 / L | Server pagination/filtering; просрочки, риски, approvals, conflicts; explainable source links |
| M3-07 | PRODUCT | Реализовать digest и notifications | M3-03, M3-06 | P1 / M | Идемпотентный digest; channel preferences; no duplicate notification; content policy и audit |
| M3-08 | PRODUCT | Завершить lifecycle договоров и версий | GATE-09 | P1 / H | Создание/редактирование/архивирование с immutable versions; удаление не разрушает evidence/history; связанные документы открываются из Contract card/graph |
| M3-09 | PRODUCT | Завершить Company/Person/Contact resolution | GATE-03 | P1 / H | Mailbox/project-scoped identity, duplicate/conflict review, manual correction, history и tenant isolation |
| M3-10 | PRODUCT | Добавить project-wide search и saved views | GATE-13, M3-08, M3-09 | P1 / M | Поиск по имени/типу/дате/project/contract/counterparty, permission-filtered results и stable pagination |
| M3-11 | PRODUCT | Провести MVP3 acceptance | M3-01..M3-10 | P1 / H | Synthetic + PostgreSQL + browser + selected live channel; restart/replay/correction; no unapproved external effect |

### E. MVP4 — исполнение и финансы

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| M4-01 | OWNER | Утвердить финансовые DTO, роли и подтверждение оплаты | OWN-02, OWN-04 | P1 / H | События invoice/payment/advance/retention, stage link, correction и thresholds утверждены; банковская выписка не обязательна |
| M4-02 | PRODUCT | Извлекать условия договора с evidence | M4-01, GATE-05 | P1 / H | Стоимость, аванс, удержания, сроки и стороны имеют source/version/locator/confidence; low confidence review |
| M4-03 | PRODUCT | Реализовать ГПР и immutable baseline versions | M4-01 | P1 / H | Baseline versioned/approved; этапы связаны с Contract/Task/Evidence; изменение создаёт новую версию |
| M4-04 | PRODUCT | Реализовать plan/fact исполнения | M4-03 | P1 / M | Факт вводится/подтверждается с evidence; пересчёт не переписывает baseline; audit complete |
| M4-05 | PRODUCT | Реализовать бюджет и ДДС | M4-02, M4-03 | P1 / H | План/факт, календарь, currency/rounding, contract/stage/source links, CAS и audit |
| M4-06 | PRODUCT | Добавить user-confirmed payment event | M4-05 | P1 / H | Только пользователь с правом подтверждает оплату; invoice ≠ paid; duplicate confirmation idempotent; correction отдельным event |
| M4-07 | PRODUCT | Реализовать закупки и заявки | M4-03, M4-05 | P1 / H | Request/order/approval/supplier/status/amount/evidence; финансовое действие не AUTO |
| M4-08 | PRODUCT | Реализовать поставки и закрывающие акты | M4-07 | P1 / H | Delivery/acceptance/act versions, quantities, discrepancies и evidence связаны с contract/stage/payment |
| M4-09 | PRODUCT | Добавить прогноз сроков и cash gaps | M4-04..M4-08 | P1 / H | Факты отделены от модели; assumptions/model version/confidence/evidence; no automatic payment/action |
| M4-10 | PRODUCT | Провести financial security/acceptance | M4-01..M4-09, LEG-05 | P1 / H | Rounding/concurrency/reversal/permissions, manual confirmation, immutable ledger и audit проходят; юрист подтверждает границы |

### F. MVP6 — федерация и enterprise deployment

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| M6-01 | PRODUCT | Формализовать Adapter Capability Contract | GATE-09 | P1 / M | read/write/search/delta/webhook/attachment/version/permission/rate-limit/health capabilities и conformance tests |
| M6-02 | PRODUCT | Довести Яндекс 360 adapter family | M6-01, GATE-04 | P1 / M | Disk/Mail/Calendar/Tasks по выбранному scope проходят один core conformance без provider fork logic |
| M6-03 | PRODUCT | Добавить следующий mailbox/calendar/task provider | OWN-03, M6-01 | P2 / M | Выбранный VK/Microsoft/Exchange adapter проходит identity/origin/dedup/approval/reconciliation tests |
| M6-04 | PRODUCT | Добавить enterprise storage adapter | OWN-05, M6-01 | P2 / H | S3/SharePoint/fileserver/on-prem source сохраняет canonical identity/version/ACL/residency и stale state |
| M6-05 | PRODUCT | Завершить AIProviderAdapter conformance | OWN-04, OWN-05 | P1 / H | Local-only, external, redacted, metadata-only modes; provider/model/prompt/evidence audit; external switch-off |
| M6-06 | PRODUCT | Поддержать корпоративный/local AI endpoint | M6-05 | P2 / H | Нет data egress в local-only; structured-output validation; timeout/fallback; no business action bypass |
| M6-07 | PRODUCTION | Создать private/on-prem deployment profile | OWN-05, GATE-07 | P1 / H | Offline-capable install, external dependencies list, secrets/keys/backup, sizing и upgrade/rollback protocol |
| M6-08 | SECURITY | Реализовать исполнимую data-residency/DLP policy | OWN-05, M6-04..M6-06 | P1 / H | Policy blocks forbidden copy/AI egress before read; stable redaction; reason/audit; negative corpus |
| M6-09 | PRODUCT | Унифицировать federated sync/staleness/conflicts | M6-01, M6-04 | P1 / H | Delta/poll/webhook, last_seen, version mismatch, deleted/unavailable source и replay idempotency |
| M6-10 | PRODUCT | Провести provider-independent MVP6 acceptance | M6-02..M6-09 | P1 / H | Один и тот же core scenario проходит минимум на двух provider families и в local-only deployment |
| M6-11 | PRODUCT | Довести Telegram до ChannelAdapter conformance | M6-01, OWN-03 | P1 / M | Нет отдельной business logic/DB; те же permissions/approval/audit; text/file ingress, summaries и web links без dangerous bypass |

### G. 1.0+ — enterprise intelligence

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| V10-01 | PRODUCT | Расширить типы ContextRelation | M3-11, M4-10 | P2 / M | Project/Contract/Document/Clause/Message/Party/Task/Deadline/Decision/Change/Invoice/Payment с hypothesis/confirmed history |
| V10-02 | PRODUCT | Добавить context query/explanation API | V10-01 | P2 / M | Traversal с tenant ACL, depth/limit, evidence reasons, historical as-of и bounded latency |
| V10-03 | OWNER | Решить, нужен ли отдельный graph engine | V10-02 | P2 / H | ADR сравнивает relational и graph storage; migration только при доказанном bottleneck, без смены business IDs |
| V10-04 | PRODUCT | Ввести Company Memory object/lifecycle | V10-01, OWN-05 | P2 / H | Provenance, scope, owner, evidence, confirmation, expiry/forgetting; AI hypothesis не становится memory автоматически |
| V10-05 | PRODUCT | Реализовать promotion/correction памяти | V10-04 | P2 / H | Human-confirmed correction приоритетнее AI; old value append-only; revoke/retention удаляет доступ, не audit |
| V10-06 | PRODUCT | Реализовать memory retrieval с policy | V10-04, V10-05 | P2 / H | Tenant/project/user scope, purpose, evidence, freshness и residency проверяются до retrieval |
| V10-07 | PRODUCT | Зафиксировать AI Agent contract | M6-05, V10-02 | P2 / H | Agent использует Trust Loop, не имеет собственного ledger/queue/authority и не исполняет вне capabilities |
| V10-08 | PRODUCT | Добавить специализированных agents по одному | V10-07 | P2 / H | Каждый agent имеет узкий purpose, corpus, permissions, cost/latency budget, kill switch и separate acceptance |
| V10-09 | PRODUCT | Реализовать enterprise policy simulation | OWN-04, M6-08 | P2 / H | Versioned policy, dry-run, conflict explanation, staged rollout, rollback и immutable decisions |
| V10-10 | OWNER | Определить пределы AUTO в 1.0+ | V10-09 | P3 / H | Только explicit low-risk allowlist; high-risk/finance/legal/payment/destructive остаются CONFIRM/DENY без отдельного legal/security decision |
| V10-11 | PRODUCT | Создать graph/ledger explanation UI | V10-02, V10-05 | P2 / M | Пользователь видит source, evidence, AI reasoning boundary, approver, effect, outcome и compensation history |
| V10-12 | PRODUCT | Добавить enterprise analytics/forecasting | M3-11, M4-10, V10-06 | P2 / H | Dataset provenance, model/version, explainability, no silent action, quality/drift monitoring |
| V10-13 | PRODUCT | Развить knowledge center | V10-04, V10-06 | P2 / M | Подтверждённые знания, source citations, access/retention и correction workflow; no uncontrolled training |
| V10-14 | PRODUCT | Развить mobile/PWA и background upload | PROD-05, GATE-12 | P2 / M | Resumable encrypted chunks, offline-safe UX, no plaintext cache, permission/session recovery |
| V10-15 | PRODUCT | Добавить плановые функции AI Secretary | M3-07, V10-07 | P2 / H | Recurring action хранит timezone/schedule/evidence/policy; письма и иные внешние действия требуют CONFIRM; повтор не создаёт дубль |

### H. Legal, release и Реестр российского ПО

| ID | Тип | Задача | Depends | Приоритет / риск | Acceptance criteria |
|---|---|---|---|---|---|
| LEG-01 | LEGAL | Подтвердить цепочку исключительных прав | OWN-07 | P0 / H | Авторы/подрядчики, договоры/акты/обременения документально закрыты юристом по ИС |
| LEG-02 | LEGAL | Финализировать лицензионную модель и договоры | OWN-07, LEG-01 | P0 / H | Стороны, способы использования, территория, срок, цена, ответственность и support согласованы |
| LEG-03 | RELEASE | Собрать воспроизводимый dependency evidence | — | P0 / H | Python transitive lock с hashes; exact Node lock; digest-pinned images; layer/apt SBOM |
| LEG-04 | LEGAL | Закрыть third-party LICENSE/NOTICE | LEG-03 | P0 / H | Package-specific texts; `licenseConcluded`; LGPL/container obligations; GPL/AGPL/SSPL conclusion профильного юриста |
| LEG-05 | LEGAL | Утвердить ПДн, AI, subprocessors и retention | OWN-05 | P0 / H | Роли, основания, регионы, processors, data classes, egress и сроки оформлены; real-data pilot разрешён либо запрещён |
| LEG-06 | RELEASE | Собрать финальный handover archive | PROD-08, LEG-01..LEG-05 | P0 / H | Allowlist archive, manifest, checksum, tests/install/restore logs; нет env/keys/tokens/PII/production data |
| LEG-07 | LEGAL | Подготовить досье Реестра ПО без подачи | LEG-01..LEG-06 | P1 / H | Правообладатель, классы/ОКПД2, публичные URL, стоимость, foreign components/payments, УКЭП/ответственные; финальная проверка юриста |

## Безопасные параллельные волны

### Волна 0 — можно выполнять сразу и параллельно

- Track A: `OWN-01..OWN-07` — только решения и документы владельца.
- Track B: `LEG-01`, `LEG-03`, затем подготовка `LEG-04` — legal/release без
  изменения Core.
- Track C: подготовка стендов для `GATE-01..GATE-04` без live вызова до
  получения test accounts.
- Track D: `GATE-05`, `GATE-06`, `GATE-09`, `GATE-10`, `GATE-13`,
  `GATE-15` на синтетических данных.

Эти tracks не должны менять одну migration chain. Любые найденные дефекты
исправляются отдельными минимальными commits с regression-first.

### Волна 1 — закрытие live и production prerequisites

- `GATE-01..GATE-12` выполняются параллельно по разным adapters/QA областям.
- `PROD-01/PROD-02` выполняются последовательно одним staging/evidence owner.
- `PROD-03/PROD-04` выполняются последовательно одним mailbox owner.
- `PROD-05`, `PROD-06` и `PROD-09` могут идти параллельно после общих
  security/recovery gates.

Merge order: schema owner → backend contracts → adapter/runtime → UI →
acceptance/workflows → docs. В каждый момент должна оставаться одна Alembic head.

### Волна 2 — controlled pilot

`PROD-01..PROD-06` и `PROD-09` сходятся в `PROD-07`. В этой волне запрещено одновременно
расширять product scope: только composition, allowlist cohort, observability,
fault drills и rollback. `PROD-08` возможен лишь после legal/release gates.

### Волна 3 — MVP3

- Track M3-A: `M3-01/M3-03` — obligation/deadline.
- Track M3-B: `M3-02` — task provider mapping.
- Track M3-C: `M3-04/M3-05` — risk/decision/meeting.
- Track M3-D: UI/search `M3-06/M3-07/M3-10` начинается после стабилизации read models.
- Track M3-E: `M3-08/M3-09` — Contract и Company/Contact lifecycle.

Один schema owner сериализует миграции; `M3-11` — единый интеграционный gate.

### Волна 4 — MVP4

Сначала `M4-01`, затем параллельно `M4-02/M4-03`; после них
`M4-04/M4-05/M4-07`; далее `M4-06/M4-08/M4-09`; завершает `M4-10`.
Финансовые, договорные и платёжные действия никогда не включаются AUTO в этой
волне.

### Волна 5 — MVP6 federation

После `M6-01` adapters `M6-02/M6-03/M6-04/M6-05/M6-11` могут развиваться
параллельно по одному conformance kit. `M6-06/M6-07/M6-08/M6-09` сходятся в `M6-10`.
Provider-specific код не переносится в доменные модели.

### Волна 6 — 1.0+

После стабильных MVP3/MVP4/MVP6 параллельны три tracks:

- Context: `V10-01..V10-03`, `V10-11`;
- Memory: `V10-04..V10-06`, `V10-13`;
- Agents/Policy: `V10-07..V10-10`, затем `V10-12/V10-15`.

`V10-14` независим от graph engine, но зависит от production-safe upload.
Десятки agents, отдельный graph engine и high-risk AUTO не запускаются как
массовая программа: каждый требует отдельного доказанного бизнес-кейса.

## Общие неизменяемые ограничения для всех волн

1. Один `BackgroundJob`; не создавать вторую очередь.
2. Job payload не содержит письма, документы, base64, токены, ключи, DSN,
   absolute path или извлечённый текст.
3. Исходный federated source не изменяется до разрешённого dry-run/approval.
4. Evidence всегда pin-ит exact source/version/locator; latest fallback запрещён.
5. Low confidence, stale/revoked authority и недоступный source завершаются
   fail closed и требуют человека либо безопасной ручной обработки.
6. Approval относится только к exact immutable payload/revision/hash.
7. Authority, mailbox generation, capability и evidence проверяются перед T2.
8. External, legal, financial, payment, destructive и access actions не AUTO.
9. Undo/compensation — новое действие; история не переписывается.
10. Один migration owner на интеграционную волну; одна Alembic head.
11. Fixtures только синтетические/обезличенные; production data и credentials
    не используются в тестах.
12. Runtime PASS даётся только по точному SHA на изолированной PostgreSQL/Linux
    среде с cleanup. Static/mock/SQLite не подменяют live/runtime gate.

## Ближайшая критическая последовательность

1. Не менять технический статус MVP5: `13/13`, runtime `PASS` на `8ccc194`.
2. Параллельно выполнить `OWN-01..OWN-07`, `LEG-01/LEG-03` и подготовить
   `GATE-01..GATE-04`.
3. Закрыть `S10` через `GATE-01`; отдельно принять live Drive/Gmail/Яндекс пути.
4. Выполнить `GATE-07/GATE-08/GATE-11`, затем scoped production composition.
5. Провести controlled pilot и только после него принимать решение о
   production enable.
6. Не задерживая legal/release, открыть MVP3; MVP4 открывать лишь после `M4-01`.
7. MVP6 и 1.0+ развивать по adapter/Trust Loop contracts, не переписывая
   работающий Core без доказанного архитектурного блокера.

## Ссылки на доказательства

- [Итоговое покрытие до runtime](v54-wave3-release-reconciliation.md)
- [MVP5 product-like acceptance](v54-product-acceptance.md)
- [Process-fault S07/S08](v54-wave3-fault-gaps.md)
- [Security review](v54-wave3-security-review.md)
- [Staging hardening](v54-staging-safety-hardening.md)
- [Mailbox identity](v54-mailbox-identity-implementation.md)
- [Evidence API/UI](v54-evidence-product-api-ui.md)
- [Provider action runtime](v54-provider-action-runtime.md)
- [Autonomy authorization](v54-autonomy-authorization.md)
- [Acceptance corpus](v54-acceptance-corpus.md)
- [SBOM/LICENSE status](v54-wave3-sbom-legal.md)
- [Legal/release readiness](legal-release-readiness-result.md)

## Финальная классификация

| Gate | Статус | Что требуется дальше |
|---|---|---|
| MVP5 code/contract | **PASS 13/13** | Сохранять fail-closed и exact approval/authority contracts |
| MVP5 PostgreSQL runtime | **PASS** | Run `33872553514`, exact SHA `8ccc194`, cleanup PASS |
| Durable queue / Docker smoke / общий CI | **PASS** | Runs `33872553425`, `33872553529`, `33872553588` |
| `P04` finance | **OUT OF MVP5 / OWNER DECISION** | `OWN-02`, затем `M4-01..M4-10` при открытии scope |
| `S10` live provider | **SYNTHETIC PASS / LIVE NOT RUN** | `GATE-01` |
| Production enable | **BLOCKED** | `GATE-07/08/11`, `PROD-01..PROD-08`, отдельное разрешение владельца |
| Commercial handover | **BLOCKED** | `LEG-01..LEG-06` |
| Реестр российского ПО | **NOT READY FOR SUBMISSION** | `LEG-07`; заявление не подавать без владельца/юриста/УКЭП |
| MVP3 | **BACKLOG** | `M3-01..M3-11` |
| MVP4 | **BACKLOG** | `M4-01..M4-10` |
| MVP6 | **BACKLOG** | `M6-01..M6-10` |
| 1.0+ | **BACKLOG** | `V10-01..V10-14` |
