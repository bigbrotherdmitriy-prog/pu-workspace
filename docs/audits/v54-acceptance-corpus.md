# v5.4 — независимый acceptance corpus

Дата: 2026-09-03. Статус: **STRUCTURAL PASS / APPLICATION NOT_RUN**.

## Область и исходное состояние

Создана новая чистая worktree pu-workspace-v54-acceptance-corpus,
ветка codex/v54-acceptance-corpus, строго от
`34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
До создания одноимённая worktree/ветка отсутствовала. Reset/cherry-pick не выполнялись.
Применимых AGENTS.md в проверенных родительских каталогах и репозитории не найдено.

Основная worktree оставлена на codex/commercial-p2-yandex360,
HEAD `83774aac726acd4e27b349e9194f30783158bde8`.
Зафиксированный до и после набор незакоммиченных файлов одинаков:

```text
backend/app/api/auth.py
backend/app/api/local_upload.py
backend/app/api/workspace.py
backend/app/schema.py
backend/app/static/app.js
docker-compose.yml
frontend/index.html
```

Они не копировались, не редактировались и не включались в коммит.

Прочитаны foundation report, integration documents на точной базе и исходное ТЗ
PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx.
SHA-256 ТЗ:
`af7bfde75715345e4f32b9d7ca057812cdba7b8d8e0b6a1b105dfe20fc0d5df3`.

Использован documents skill для чтения DOCX: извлечён текст OOXML, включая
положения v5.4, без изменения документа. Визуальная вёрстка исходного DOCX не
проверялась: deliverable — JSON/TXT/MD, не переработанный DOCX.

Read-only изучены контракты/отчёты A/B/C:

- A: 7674e973401301d4d31e8561ce7875427a600869.
- B: 7edea2b5e6b362b856dfb752ee4a09ae598e12d2.
- C: f384ae533d6ac48229d2bf00aa2659b8b3895ca6.

Коммиты не переносились; их unit-тесты и общая fixture не копировались.
Ожидаемые результаты выведены из ТЗ, явных требований пользователя и foundation
invariants, а не зафиксированы по наблюдаемому поведению реализации.

## Поставлено

[Инструкция и интеграционный handoff](../acceptance/v54-corpus/README_RU.md).
[Манифест и coverage](../acceptance/v54-corpus/manifest.json).

| Категория | Количество | Диапазон |
|---|---:|---|
| content | 12 | C01–C12 |
| policy | 6 | P01–P06 |
| sequence | 10 | S01–S10 |
| Всего | 28 | Все темы пользовательского списка покрыты |

14 созданных файлов источников, 52 точных excerpts.
C01 содержит письмо с одним вложением и полный цикл трёх отдельных решений.
Сбои и отзыв доступа заданы управляющими событиями, не текстом письма.
S01 меняет наблюдение одного вложения, а не перезаписывает прежний source.

Формат каждого case включает requirement, synthetic inputs, authority,
source versions, excerpts/offsets, events, hypotheses/claims, context/claim/action
confirmation, allowed/forbidden changes, business/audit outcome, PASS и uncertainties.

Корпус использует только example.test и вымышленные реквизиты. Имена ID — псевдонимы,
не production identifiers. Нет реальных писем, клиентских файлов и credentials.
Реальные hashes — SHA-256 байтов собственных файлов, а не подставленные source IDs.
Не заявляются измеренные confidence, precision/recall или качество OCR.

## Проверки

Среда: Windows, Python 3.12.13. Только стандартная библиотека.

| Проверка | Результат |
|---|---|
| validate.py --self-test | exit 0; STRUCTURAL PASS |
| 28 cases / 14 assets / 52 excerpts | Проверены |
| 31 отрицательная mutation-проверка | Все дефекты обнаружены |
| Python -I -B, запуск из родительского каталога | exit 0; те же результаты, без product import |
| CLI --root на sources без manifest | exit 1; safe unreadable_file; без вывода содержимого |
| git diff --cached --check | Без замечаний |
| Реальное приложение/A/B/C facades | NOT_RUN — не подключались в этой задаче |
| PostgreSQL concurrency/fault | NOT_RUN — требуется отдельный harness |
| Внешние сервисы | Не вызывались |

Финальный структурный результат:

```json
{
  "structural": "PASS",
  "cases": 28,
  "categories": {"content": 12, "policy": 6, "sequence": 10},
  "assets": 14,
  "excerpts": 52,
  "validator_self_test": {"negative_checks": 31, "result": "PASS"},
  "application": "NOT_RUN",
  "postgres_fault_tests": "NOT_RUN",
  "postgres_required_cases": ["P02", "S03", "S04", "S06", "S07", "S08"],
  "owner_decision_cases": ["C07", "P04", "P06", "S10"]
}
```

Mutation checks проверяют дубли IDs/JSON keys, отсутствие ожиданий/PASS,
hash/изменение bytes, ссылки/версии/observation revision, excerpt/offsets,
attachment parent, невозможную дату, порядок событий, обязательный fault gate,
неизвестных/несовместимых кандидатов, выдуманный confidence, посторонний домен,
внешний эффект, фиктивный product PASS, утечку oracle при no-copy,
шесть вариантов небезопасных путей, malformed/nonfinite JSON.
Они выполняются в памяти, без изменения корпуса.

Валидатор дополнительно проверяет наличие/границы файлов, запрет symlink-путей,
объявленные JSON/source assets, категории/счётчики/coverage, ссылки на interface
requests. Он не является полноценным JSON Schema engine, semantic classifier,
ACL runner, secret scanner или доказательством работы приложения.

## Существенные ограничения и решения

1. **IR-01, extraction / fragments.** Подготовленные pins и DTO в фасадах A/B/C
   не доказывают извлечение смысла. Для content PASS нужно прогнать исходный текст
   через приложение, не подать ожидаемые candidates как вход.
   Whole-object evidence без разрешённого fragment/locator UX не закрывает
   проверку источника каждого извлечённого значения.
2. **IR-02, legacy identity.** S02 требует двух Message origin для одинакового
   external_id в двух mailbox/account. Global uq_message_source не обойдён.
   Безопасный отказ второго ingress — BLOCKED, не PASS целевой возможности.
   Required project остаётся intake anchor, не гипотезой/доказательством.
3. **IR-03, точность срока.** C07 требует сохранить 18:30 UTC+03:00.
   Date-only общий claim не позволяет молча отбросить время. Решение владельца
   и Task/Trust потока требуется до полной приёмки.
4. **IR-04/05, реальные транзакции.** Нужны authority/revoke guards, общий Task
   helper, pending reconciler, стабильный queue key mapping и PostgreSQL стенд.
   Корпус не создаёт новый ledger, approvals, очередь или DB модели.
5. **IR-06, запрет копирования.** P03/P05 oracle-only bytes/quotes скрываются от
   SUT. Проверяющий имеет синтетический эталон, но это не лицензия продукту на
   materialization. Проверку log/storage/cache sinks должен выполнить harness.
6. **IR-07/08/09, будущие policy cases.** S10 — fake-only UNKNOWN, P04 — оплата
   только после явного подтверждения пользователем без обязательной выписки,
   P06 — AUTO/external запрещены. Финансовое и внешнее execution не реализуются.
7. Audit labels в корпусе — семантика, не новые enum. Точную проекцию на общий
   append_audit согласует интегратор. Context, claim и action approvals независимы.
8. Срок отсутствует/неоднозначен — normalized_date=null. Похожие имена компаний,
   домен, active project и факт получения ответа/отправки не доказывают выполнение
   задачи. Corpus не расширяет пилот до полного Gmail-routing набора.

Численных confidence thresholds требования не задают. Порядок сортировки
конфликтующих кандидатов оставлен открытым; конфликт и необходимость человека
обязательны. Ни одна неопределённость не превращена в разрешение автономии.

## Инструкция интегратору

1. Забрать corpus как независимый oracle в разрешённом интеграционном потоке.
2. Сопоставить псевдонимы с общими ObjectRef, изолируя каждый case.
3. Встроить контролируемые source delivery/permissions/events через A/B/C,
   не меняя ожидания ради текущего поведения и не читая production.
4. Разделить raw-content acceptance, policy harness и PG fault harness.
5. Записать фактические PASS/FAIL/BLOCKED/NOT_RUN отдельно от corpus.
   Приложить build SHA, fault boundary и наблюдения БД без содержимого документов.
6. Не засчитывать структурный PASS, prepared pins или SQLite как product/PG PASS.

## Изменённые файлы

Только новые файлы:

- docs/acceptance/v54-corpus/.gitattributes
- docs/acceptance/v54-corpus/README_RU.md
- docs/acceptance/v54-corpus/manifest.json
- docs/acceptance/v54-corpus/validate.py
- docs/acceptance/v54-corpus/cases/content.json
- docs/acceptance/v54-corpus/cases/policy.json
- docs/acceptance/v54-corpus/cases/sequence.json
- docs/acceptance/v54-corpus/sources/ambiguous_mail.txt
- docs/acceptance/v54-corpus/sources/clear_attachment.md
- docs/acceptance/v54-corpus/sources/clear_mail.txt
- docs/acceptance/v54-corpus/sources/conflict_attachment.md
- docs/acceptance/v54-corpus/sources/conflict_date_mail.txt
- docs/acceptance/v54-corpus/sources/contact_only_mail.txt
- docs/acceptance/v54-corpus/sources/exact_number_mail.txt
- docs/acceptance/v54-corpus/sources/injection_mail.txt
- docs/acceptance/v54-corpus/sources/no_date_mail.txt
- docs/acceptance/v54-corpus/sources/payment_mail.txt
- docs/acceptance/v54-corpus/sources/relative_mail.txt
- docs/acceptance/v54-corpus/sources/revised_attachment.md
- docs/acceptance/v54-corpus/sources/same_number_mail.txt
- docs/acceptance/v54-corpus/sources/timed_mail.txt
- docs/audits/v54-acceptance-corpus.md

Новых полей/миграций нет; requests приведены в manifest, не реализованы.
Ни backend, ни frontend, ни CI, ни существующие тесты/общие документы не изменены.
Push, merge, PR, deploy и обращения к production/VPS не выполнялись.
Итоговый SHA сообщается отдельно: самоссылочный hash не включается в этот файл.
