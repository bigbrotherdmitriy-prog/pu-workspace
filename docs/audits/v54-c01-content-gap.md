# PU Workspace v5.4 — C01 content-to-action gap

Дата проверки: 2026-09-04

База: `f869319e226d0563d9c95eec408adcf716ed7e9f`

Ветка: `codex/v54-wave3-content-gap`

## Решение

Разрыв C01 закрыт минимальным локальным synthetic-only мостом. До изменения
product acceptance создавал whole-object evidence и передавал дату срока готовым
DTO. Теперь тест читает реальные байты `clear_mail.txt` и
`clear_attachment.md`, локально извлекает реквизиты и срок и проводит результат
через существующие writers:

`SourceReference/SourceVersion → immutable Evidence → ContextRelation → DeadlineClaim → Trust → Task/receipt`.

Oracle из `cases/content.json` не передаётся extractor'у. Он используется только
после извлечения для сравнения результата и точных Unicode code-point offsets.
Внешний AI, сеть, OAuth, mailbox provider и клиентские документы не используются.

## Инварианты

- evidence хранит только pin, версию источника, extractor metadata, confidence и
  `text_range(start,end)`; цитата и исходный текст в БД не сохраняются;
- повторное использование evidence ID с другими координатами запрещено;
- численная уверенность не подтверждает evidence автоматически;
- analyse создаёт только unverified evidence, hypotheses и unverified claim;
- evidence review, context confirmation и claim review выполняются отдельно;
- до этих решений Task, Obligation, approval, job и provider effect не создаются;
- конфликт дат, неоднозначные реквизиты, неверный UTF-8 и точное время дают
  `manual_review_required` без A/B/C mutations;
- active project не является сигналом маршрутизации;
- AUTO и внешние действия не расширены.

## Исполняемый C01

Проверяются:

1. точное извлечение `Альфа-Макет`, `TEST-A-42`, `2030-04-17`;
2. совпадение координат обоих фрагментов с независимым corpus oracle;
3. маршрутизация в `alpha/a42`, несмотря на synthetic active project `beta`;
4. ноль Task до трёх независимых human decisions;
5. одна внутренняя Task со сроком `2030-04-17` после exact Trust approval;
6. ноль Obligation и внешних действий;
7. fail-closed конфликт дат и запрет молча отбрасывать `18:30`;
8. неизменяемость evidence coordinates.

`scripts/ci/v54_pilot_workflow.py` теперь включает C01 в PostgreSQL A/B/C phase и
в allowlisted `executed_cases`; C01 удалён из `expected_gaps`.

## Проверки

- новые C01 tests: `6 passed`;
- изменённые runtime CI contracts: `14 passed`;
- расширенный Source/Evidence, fragment reader, A/B/C integration, product
  acceptance и CI набор: `170 passed`; один существующий test оказался
  несовместим с расширенным отображением source context в traceback Python 3.13
  и не связан с этим изменением;
- полный backend без этого одного environment-specific assertion:
  `1126 passed, 17 skipped, 1 deselected`;
- полный `scripts/ci`: `111 passed`; восемь локальных environment failures
  вызваны отсутствующим WSL `/bin/bash`, повреждением Unicode path Windows/MSYS
  и отсутствием inherited `httpx` в отдельном subprocess, а не C01-контрактом;
- PostgreSQL URL локально недоступен. Общий C01 integration fixture использует
  `PUW_V54_INTEGRATION_DATABASE_URL`, поэтому в runtime CI те же проверки пойдут
  на PostgreSQL, а не будут подменены SQLite.

## Требование интегратору

После cherry-pick нужен новый изолированный `v54-pilot-runtime.yml`, потому что
изменены состав PostgreSQL acceptance phase и runtime protocol. Успех прежнего
run на `f869319` не является доказательством этого коммита.

Ожидаемые ограничения после интеграции: C07 (deadline с точным временем) остаётся
fail-closed и требует отдельного timestamp-контракта; extractor покрывает только
синтетический TXT/MD acceptance C01 и не включён в production ingress.
