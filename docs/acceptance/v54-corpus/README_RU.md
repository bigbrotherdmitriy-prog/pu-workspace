# Независимый приёмочный корпус v5.4

Статус: **структурно проверено; продуктовая приёмка не выполнена**.

28 вымышленных случаев: content — 12, policy — 6, sequence — 10.
14 файлов TXT/MD, 52 точных фрагмента. Нет клиентских документов, credentials,
production IDs, запросов к сервисам, PDF/DOCX и OCR benchmark.

База: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
Корпус не включает A/B/C-коммиты и не импортирует backend, unit-тесты или foundation fixture.

## Запуск

Из корня этой worktree, Python 3.10+ (только стандартная библиотека):

```powershell
python docs/acceptance/v54-corpus/validate.py
python docs/acceptance/v54-corpus/validate.py --self-test
```

Никакие .env, сервер, PostgreSQL или ключи не нужны. Валидатор можно запускать из
любого каталога: по умолчанию root определяется по расположению скрипта.
`--root <путь-к-копии-корпуса>` проверяет другую копию.
Не передавайте ему production-каталог.

Успех: exit 0, JSON с structural=PASS, cases=28, assets=14, excerpts=52.
Self-test намеренно повреждает данные **в памяти**, не меняя файлы;
все отрицательные проверки должны обнаружить дефект.
Ошибка: exit 1 и безопасный код/место ошибки без тела документа.
Неверные аргументы CLI: exit 2. При hash mismatch восстанавливайте проверяемый
файл из Git; не «исправляйте» ожидаемый hash ради зелёного результата.

Это не приложение, не генератор approvals и не исполнитель инструкций в письмах.
Счётчик structural PASS не является процентом готовности продукта.

## Состав и формат

- manifest.json: точная база, происхождение требований, SHA-256 источников,
  вымышленный каталог сущностей, coverage и interface requests.
- cases/content.json: extraction oracle, а не вывод текущего парсера.
- cases/policy.json: управляющие политики, ACL и решения владельца.
- cases/sequence.json: последовательности доставки, corrections, replay и fault.
- sources/: недоверенные синтетические письма и вложения.
- validate.py: самостоятельная структурная проверка.

Каждый case содержит предпосылки, permissions, источники/версии, точные excerpts,
события, ожидаемые hypotheses/claims, три вида подтверждения, разрешённые/
запрещённые изменения, business/audit outcome, PASS-условия и неопределённости.
Числа business относятся к одной изолированной истории **после указанной phase**,
а не к каждому промежуточному шагу.

Все идентификаторы — локальные псевдонимы корпуса, не общие DTO.
Интегратор создаёт новые реальные ObjectRef/UUID/integer ID на каждый случай.
manifest.entities задаёт отношения: a42 принадлежит alpha, b43 — beta и т.д.
При C06 список contact_projects_override заменяет membership только в этом case.
inputs.acl_variants в P05 — четыре самостоятельных отрицательных прогона.

Sources ссылаются на immutable observation IDs с observation_revision=1.
r2 — новая observation того же logical_source, а не изменение старой записи.
inputs.attachments связывает attachment с сообщением. В S01 два файла —
**версии одного вложения**, не два вложения одновременно: вторую версию следует
скрыть до события control_publish_new_source_observation.
display_name не является identity и может совпадать.

Offsets: индексы Unicode code points, от нуля; start включён, end исключён.
Применять к декодированным UTF-8 байтам без нормализации, LF, без BOM.
SHA-256 считается от **реальных байтов файла**, не от quote, Git blob или ID.
.gitattributes сохраняет LF при checkout на Windows.
SHA исходного ТЗ — только provenance: файл ТЗ не поставляется и валидатор
не выходит за каталог для повторной проверки этого внешнего файла.

## Матрица покрытия

| Категория | Cases | Что проверяется |
|---|---|---|
| Содержание | C01–C06 | Однозначность, два проекта, одинаковые номера, 12/3124, общий контакт, конфликт контакта |
| Содержание | C07–C12 | Точный/относительный/противоречивый/отсутствующий срок, вложение против письма, injection |
| Политики | P01–P03 | Недоступность, revoke, запрет локальной копии и фрагментов |
| Политики | P04–P06 | Подтверждение оплаты человеком, четыре уровня ACL, запрет AUTO |
| События | S01–S04 | Новая версия, два mailbox, повтор доставки, correction/CAS/late analysis |
| События | S05–S10 | Payload после approval, receipt replay, два crash boundary, cancel, UNKNOWN external |

Словарь требований в manifest содержит ссылки на §31/35 ТЗ и foundation invariants.
Source text может содержать указания «игнорировать правила». Это предмет
отрицательного теста, не инструкции оператору или валидатору.

## Подключение интегратором A/B/C

1. Валидировать неизменённый корпус. Для каждого case создать изолированный
   tenant/connection/project/contract/actor graph по псевдонимам. Не использовать
   текущие аккаунты, реальные почтовые ящики, production IDs или внешние сервисы.
2. Сконструировать permissions и clock как управляющие входы harness.
   ACL/revoke/crash — реальные события стенда, **не строки письма**.
   Не передавать expected/quote oracle в AI/parser как подсказку ответа.
3. A: зарегистрировать synthetic ConnectionIdentity и Message/attachment origin,
   версии и evidence через общий facade. Не создавать второй credential registry.
   Передавать только разрешённые bytes. Оператору доступны evidence лишь при
   актуальных правах и разрешённой политике fragments.
4. Для content cases сначала выполнить реальное извлечение из входного TXT/MD,
   затем сравнить с oracle. Передача подготовленных candidates/claims из JSON
   может проверить wiring B/C, но **не засчитывается как приёмка extraction**.
5. B: hypotheses и независимое confirm/correct primary project/contract.
   При смене проекта старый contract инвалидируется. Проверять expected_context_version
   и CAS обеих relations. Сохранять origin mailbox и историю, не делать глобальное
   правило из единичной correction.
6. C: отдельно проверить/подтвердить DeadlineClaim; затем freeze точного action,
   отдельно approve и исполнить через общий Trust/Task helper. Context confirmation
   не является ни проверкой срока, ни разрешением действия.
7. C01: после analyse Task=0; после context confirm Task=0; после claim review Task=0;
   только после exact action approval/execute Task=1. Черновик ответа не отправлен.
   Receipt проецируется B идемпотентно, без второго Task execution.
8. Применить events по порядку; проверить промежуточные запреты, финальный business
   и append-only audit. Для отрицательных попыток не использовать callback,
   который «возвращает ожидаемый отказ» без вызова проверяемой границы.
9. Сохранить отдельный результат приложения: case_id, build SHA, harness версия,
   status=PASS/FAIL/BLOCKED/NOT_RUN, безопасная причина, refs проверок.
   Не менять expected или structural/application-поля исходного корпуса.

Hypotheses в JSON описывают output анализа; confirmed context в business —
финал событий. Если состояние контекста меняется явно (S04), hypothesis section
описывает конечные assertions. Номер договора сравнивается как точный реквизит
с контекстом отрицания; похожее имя компании не служит правилом объединения.
Уровни confidence не заданы: null означает «не измерено», не вероятность 0.

Audit.required_observations — семантические требования, не новые enum.
Использовать существующий append_audit; недостающие события согласовать с владельцем.
Audit/log/job payload не должны содержать письма, файлы, base64, цитаты или secrets.
В job payload — разрешённые ссылки/версии/идемпотентный ключ, не содержимое.

### Недоступные и запрещённые источники

- P01/P02: excerpts — oracle для проверяющего; после control event не возвращать
  их через SUT, даже из старого cache.
- P03/P05: oracle_only никогда не подаётся тестируемому пользователю/parser.
  У P03 запрещено получать выводы по скрытому содержимому. Наличие файлов у
  независимого оценщика не разрешает продукту их копировать/сохранять/отправлять.
- Инспекция log/storage sinks — обязанность будущего harness. Валидатор сам не
  способен доказать, что приложение не сохранило документ.
- Возврат явного policy denial может быть правильным результатом P03, но не
  доказывает поддержку работы с remote content без копирования.

### Legacy ограничения

В точной базе Message требует project, а uq_message_source глобален.
Разрешённый legacy_intake_project=alpha — только уже выбранный пользователем
входной anchor, не confirmed context и не аргумент классификации. Он не должен
отсеивать beta в C02/C03/C05. Нельзя фабриковать общий проект для «unassigned».

S02 целевым результатом требует два независимых origin. Пока это невозможно,
фиксировать integration BLOCKED. Безопасный отказ второго ingress полезен для
изоляции, но не PASS mailbox-scoped identity. Не менять source ID префиксом и не
переносить старые сообщения автоматически. Для legacy origin без достоверного
mailbox нужен отдельный migration/verification plan (IR-02).

### PostgreSQL и fault gates

P02, S03, S04, S06, S07, S08 требуют отдельного PostgreSQL concurrency/fault прогона.
SQLite, mock, один процесс и выставленный флаг «crashed» не являются доказательством.

- S03/S04/S06: независимые подключения/транзакции, барьеры; assert SQL rows/history
  после обеих попыток, включая stale CAS и replay.
- P02: commit revoke перед mutation guard; проверить авторитетную версию прав.
  Отзыв после завершённого эффекта не объявлять автоматическим undo.
- S07: kill отдельного процесса после commit pending intent, до enqueue; новый
  процесс восстанавливает из БД, не из памяти теста.
- S08: kill worker после атомарного Task/history/receipt/audit commit и до queue
  completion; истёкший lease забирает другой worker, effect не повторяется.
- Очередь, ledger, Task helper и authority предоставляет интегратор; корпус их
  не реализует. Не использовать production БД для fault tests.

## Открытые решения и границы приёмки

| Состояние | Cases / interface request | Значение |
|---|---|---|
| Проверено структурно | Все 28 | JSON, IDs, ссылки, версии, реальные hashes и offsets |
| Планируется приложением | Все pilot cases | Никакого product PASS в этом пакете |
| Требуется PostgreSQL fault test | P02, S03, S04, S06–S08 | Отдельный реальный стенд |
| Блокируется интеграцией | S02 / IR-02 | Legacy uniqueness, required project |
| Требуется решение владельца | C07 / IR-03 | Timestamp/timezone против общего date-only claim |
| Вне executable pilot | P04 / IR-08 | Пользовательское подтверждение оплаты без обязательной выписки; никакого ДДС execution |
| Вне executable pilot | P06 / IR-09 | AUTO и внешние actions не включать |
| Только fake-state oracle | S10 / IR-07 | UNKNOWN не разрешает слепой retry; никаких внешних вызовов |

A/B/C уже задают ссылки, hypotheses и Trust boundaries, но не заменяют raw-content
extractor, durable ACL authority и общий реальный Task mutation helper.
Эти зависимости перечислены в manifest.interface_requests (IR-01…IR-09).
Корпус не утверждает, что каждый ID запроса требует нового поля модели: сначала
согласовать доступный общий контракт, только затем отдельное интеграционное изменение.

Для полноценного PASS требуются все условия case, не только безопасный отказ.
Неисполнимый сегодня сценарий остаётся BLOCKED/NOT_RUN, а спорное ожидание —
решением владельца; ожидаемый результат не подгоняется под текущую реализацию.
