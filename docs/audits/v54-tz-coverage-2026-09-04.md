# PU Workspace v5.4 — матрица покрытия ТЗ и следующий безопасный срез

Дата аудита: 2026-09-04
Ветка: `codex/v54-final-integration`
Проверенный локальный HEAD: `33ba65a982bb60d4b51e4395778a4318d13220d7`
Решение: **MVP5 Pilot Ready — CONDITIONAL / production enable BLOCKED**

## Источник и границы вывода

Источником требований был файл
`PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`. Имя файла содержит
`v5_4`, но титульная часть и §31/§40 называют документ версией 5.1. До
формальной выдачи владельцу нужно утвердить одно каноническое обозначение
версии; этот аудит использует название «v5.4» как имя текущего пакета работ.

Документ одновременно содержит:

- полный Product Scope;
- узкий Implementation Scope первого вертикального среза (§32–§40);
- стратегический MVP5 Pilot Ready (§§200–247).

Наличие функции в Product Scope не считается доказательством реализации. В
статус «реализовано» ниже включён только код текущей ветки с тестом. Design,
mock, fixture и подготовленный workflow отмечены отдельно.

## Обозначения

- **CODE PASS** — функция реализована и проверена локальными тестами;
- **RUNTIME PASS** — выполнен PostgreSQL/process-fault runtime;
- **PARTIAL** — реализована безопасная часть или synthetic pilot;
- **DESIGN ONLY** — есть контракт/макет/план, но нет product runtime;
- **BLOCKED** — критерий нельзя честно принять без отдельной интеграции,
  решения владельца или внешнего runtime.

## Семь стратегических принципов v5.4

| Принцип | Статус | Доказательство | Что отсутствует |
|---|---|---|---|
| Federated Source-of-Truth | PARTIAL | Source/Version/Current с CAS и fail-closed resolver; `v54-source-evidence-pilot.md` | Реальный provider origin, no-copy policy до скачивания, encrypted staging и stale-provider UI |
| Evidence Engine | PARTIAL | Immutable Evidence, version pin, locator и assessment в synthetic pilot | Реальное извлечение fragment/page/clause/table-cell из разрешённого источника и UI/API чтение с ACL |
| Project Context Graph | PARTIAL | Реляционный `ContextRelation`, hypothesis/confirm/correct с CAS и историей | Полный production graph объектов, реальный mailbox origin и multi-mailbox identity |
| Communication-to-Action | PARTIAL | Synthetic Message → context → deadline claim → approved internal Task → receipt/projection/audit | Реальный входящий канал, attachment staging, draft response, adapter execution и tracking/escalation |
| Autonomy Levels | PARTIAL | CONFIRM, отдельные review/approval, live Authority перед T2; high-risk fail-closed | AUTO не утверждён и выключен; organization policy UI/API и production roles не подключены |
| Reversible/Compensating Actions | PARTIAL | Отдельная подтверждаемая отмена внутренней Task с новым receipt/audit | Матрица реальных adapter actions; compensating follow-up для необратимого email |
| Action Ledger | PARTIAL | Append-oriented audit, sealed revision/hash, approval, receipt и context projection | Сквозной внешний provider ID/outcome и production-readable chain UI |

## Приёмочные сценарии MVP5 (§220–247)

| Сценарий | Статус | Фактическое покрытие |
|---|---|---|
| Письмо подрядчика + вложение → проект/договор → evidence → срок → Task/ответ → approval → audit | PARTIAL | Synthetic attachment/message и internal Task проходят; реального письма, draft/send и provider effect нет |
| Повторная доставка не создаёт дубль | CODE PASS | Стабильные command key, queue binding, один Task/receipt/projection; PostgreSQL повтор после нового fix требует runtime |
| Пользователь исправляет проект/договор | CODE PASS | CAS correction и история ContextRelation покрыты synthetic regression |
| High-risk действие без approval блокируется | CODE PASS | Trust/Authority fail-closed, service worker не approve, global admin не получает неявного права |
| Изменённый payload требует нового approval | CODE PASS | Seal/revision/hash инвалидируют старое решение |
| Срок показан с source/version evidence | PARTIAL | Synthetic pin/locator есть; реальный extractor и fragment ACL не подключены |
| `create-internal-task=AUTO`, external send=CONFIRM | BLOCKED | AUTO намеренно выключен; требуется явное решение владельца и отдельный policy/runtime срез |
| Отмена reversible Task — отдельное audited action | CODE PASS | Реализовано в synthetic CONFIRM pilot |
| Email irreversible + compensating follow-up | DESIGN ONLY | Описано в UX/контракте, product execution отсутствует |
| Источник остаётся у клиента без запрещённой копии | PARTIAL | Federated reference существует; staging assessment запрещает прямой перенос старого staging fork |
| Недоступный/устаревший provider source виден явно | PARTIAL | Backend resolver deny/assessment есть; product UI и live provider сценарий не приняты |

## Первый вертикальный срез (§263–278)

Текущий repository содержит документное ядро, Google/Яндекс storage adapters,
snapshot/analysis/proposal и durable jobs, однако ночной аудит не использовал
реальные OAuth credentials или клиентские данные. Поэтому следующие пункты
нельзя повысить до текущего RUNTIME PASS только на основании unit regression:

- живой OAuth и выбор папки Google Drive;
- неизменность реального исходника до approval;
- dry-run/apply/rollback одного реального объекта;
- restart recovery с реальным provider interaction;
- ручной acceptance без потери данных;
- performance smoke на 1 000/10 000 объектов.

Имеющиеся storage/Gmail/queue regression остаются доказательством кода, но не
подменяют provider acceptance. Это соответствует §254: scope не расширяется
автоматически.

## Что подтверждено текущим кандидатом

- одна Alembic head `a54f001c0a02`;
- DB-backed `AuthorityState`/`AuthoritySnapshot`, монотонный epoch и CAS;
- проверка authority непосредственно перед T2;
- CONFIRM-only и synthetic-only;
- job payload без содержимого документов и писем;
- immutable Source/Evidence pins и отдельная manual review оценка;
- Context hypothesis/confirm/correct и защита от late analysis;
- один internal Task, receipt, audit и идемпотентная projection;
- отдельная подтверждаемая отмена Task;
- structural corpus: 28 cases / 14 sources / 52 excerpts;
- UX mock проверен отдельно, но не подключён к `frontend/src/App.tsx`;
- полный backend после последнего process-spawn fix: 754 passed / 9 skipped;
- v5.4 target: 261 passed / 1 PostgreSQL skip;
- `scripts/ci`: 91 passed.

## Блокеры Pilot Ready

| ID | Блокер | Владелец следующего решения | Безопасный следующий результат |
|---|---|---|---|
| V54-RUNTIME-01 | Новый spawn-safe fix не прогнан в GitHub PostgreSQL process-fault | Интегратор/CI | Зелёный `v54-pilot-runtime` artifact с cleanup PASS |
| V54-MBX-01 | Нет durable mailbox/account identity и append-only origin history | Backend integration | Аддитивная модель/миграция + synthetic cutover regression; production data не читать |
| V54-STAGE-01 | Local upload/Gmail attachment не связаны с новой SourceVersion через безопасный staging | Security/storage integration | Selective port по assessment; не cherry-pick старого staging fork |
| V54-EVIDENCE-01 | Нет production fragment reader с page/clause/cell ACL | Document/OCR integration | Read-only extractor interface + synthetic fixtures + deny tests |
| V54-UI-01 | Проверен standalone mock, но нет product API/UX | Frontend integration | Подключить только после стабилизации API authority/evidence projections |
| V54-AUTO-01 | AUTO policy не утверждён | Владелец продукта + security | Решение по единственному low-risk типу либо явное сохранение CONFIRM-only |
| V54-PROVIDER-01 | Нет production-like end-to-end channel/provider effect | Integration acceptance | Тестовый provider/fake effect с external ID; затем отдельный live sandbox acceptance |

## Разрешённый порядок дальнейшей реализации

1. Закрыть `V54-RUNTIME-01` без изменений Core: отправить текущие локальные
   коммиты и повторить изолированный workflow после отдельного разрешения.
2. На подтверждённой runtime-базе реализовать mailbox identity cutover отдельным
   аддитивным срезом; сначала dry-run inventory и synthetic migration tests.
3. Затем selective staging integration: policy до скачивания, opaque staging ID,
   encrypted shared representation и привязка к точной SourceVersion.
4. После этого подключить реальный evidence reader и product UI projection.
5. Завершить одним production-like Communication-to-Action acceptance на
   тестовом provider, сохраняя CONFIRM.
6. AUTO рассматривать только после отдельного решения владельца; external,
   financial, legal и destructive actions не включать.

Параллельно выполнять пункты 2–4 на одной общей migration head нельзя: они
пересекаются в Message/SourceVersion/Authority lifecycle и требуют одного
интегратора. Независимо можно продолжать только synthetic corpus/UX/accessibility
и read-only documentation checks.

## Итог

Текущая ветка является сильным **synthetic CONFIRM integration candidate**, но
ещё не соответствует честному статусу «MVP5 Pilot Ready» из ТЗ. После зелёного
process-fault CI будет закрыта runtime-достоверность ядра пилота; mailbox,
encrypted staging, реальный evidence reader и product UX останутся отдельными
обязательными срезами. Production enable до их завершения запрещён.
