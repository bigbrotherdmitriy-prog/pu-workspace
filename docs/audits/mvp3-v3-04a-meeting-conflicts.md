# MVP-3 V3-04a — предупреждения о конфликтах встреч

Дата: 2026-09-19

Ветка: `codex/mvp3-v3-04-meeting-conflicts`

Implementation commit: `858ea23b2a6d36e8a83e15e6cbbee11d75ff9135`

Статус: **IMPLEMENTATION PASS / POSTGRESQL PASS / LIVE BROWSER PASS**

## Реализованная граница

- nullable `meetings.duration_minutes` с диапазоном 1–10080 минут;
- новая tenant-scoped связь `meeting_participants` с ровно одной идентичностью
  (`user_id` XOR `contact_id`), уникальной в пределах встречи;
- сохранение старого свободного текста `meetings.participants` только для
  отображения и обратной совместимости;
- динамическая conflict projection без отдельной mutable-таблицы конфликтов;
- полуоткрытые интервалы `[start, end)`, поэтому смежные встречи не конфликтуют;
- предупреждение не блокирует создание встречи;
- конфликт другого проекта той же организации не раскрывает проект, встречу и
  название пользователю без членства;
- UI для длительности и структурированных участников, плюс явное предупреждение
  после успешного создания.

V3-04b (помещения и другие ресурсы) не реализован и зафиксирован отдельно в
`docs/audits/mvp3-v3-04b-resource-conflicts-backlog.md`.

## Десять проверок приёмки

| № | Проверка | Доказательство |
|---|---|---|
| 1 | Частичное пересечение одного участника предупреждает, обе встречи созданы | `test_overlapping_meetings_warn_but_both_are_created` (case 10:00/10:30) |
| 2 | Вложенный интервал предупреждает | тот же parametrized test (180/30 минут) |
| 3 | Одинаковые интервалы предупреждают | тот же parametrized test (одинаковое начало/длительность) |
| 4 | Смежные полуоткрытые интервалы не конфликтуют | `test_adjacent_meetings_use_half_open_intervals` |
| 5 | Разные участники в одно время не конфликтуют | `test_same_time_with_different_participants_does_not_warn` |
| 6 | Отменённая встреча исключается | `test_cancelled_meeting_does_not_conflict` |
| 7 | Legacy-текст не угадывается как структурированный участник | `test_legacy_text_participants_remain_visible_but_are_not_guessed` |
| 8 | Tenant/project isolation | `test_participants_must_belong_to_the_target_project`, `test_cross_project_conflict_is_redacted_without_access`, PostgreSQL test с двумя tenants |
| 9 | Миграция реальной непустой PostgreSQL-БД | изолированный `puw-mvp2-live-test`: `f91c2d4e6a80 -> a72d4e6f8b91`; legacy row сохранился с `duration_minutes=NULL` |
| 10 | Browser E2E: две встречи создаются, warning видим | synthetic Playwright `meeting-conflicts.e2e.ts` PASS; live Chromium против реального API/PostgreSQL PASS, созданные IDs 2 и 3, затем очищены |

Дополнительно контактный вариант проверяет
`test_active_project_contact_can_participate_in_conflict`, а DTO-контракт,
default duration и запрет дублей — `test_duration_contract_is_backward_compatible_and_bounded`.

## Прогоны

- целевой backend regression: `24 passed`;
- Alembic/head regression: `121 passed, 4 skipped`;
- CI head-pin regression: `19 passed`;
- полный backend: `1733 passed, 58 skipped, 0 failed, 0 errors` за 259.97 s;
- frontend Vitest: `213 passed` (40 файлов);
- TypeScript `pnpm check`: PASS;
- E2E TypeScript `pnpm check:e2e`: PASS;
- production build: PASS;
- synthetic browser E2E: `1 passed`;
- real PostgreSQL gate: `1 passed in 5.39s`;
- live browser E2E: PASS, `conflict_count=1`, обе встречи прочитаны обратно;
- единственная Alembic head: `a72d4e6f8b91`;
- `git diff --check`: PASS;
- secrets scan: 0 находок.

Первый локальный полный pytest был недействителен из-за Windows ACL на
`C:\Users\dpush\AppData\Local\Temp\pytest-of-dpush`. Финальный прогон выполнен
с отдельным `--basetemp` на диске D; setup errors отсутствуют.

## Runtime-стенд и очистка

Проверка выполнена не на production, а в отдельном Compose-проекте
`/opt/puw-mvp2-live-test` на image tag точного implementation commit.
Предыдущие исходники сохранены в rollback-каталоге. После доказательства удалены
ровно одна migration-fixture встреча и две browser-E2E встречи; контрольный
остаток V3-04a test meetings равен 0. Screenshot сохранён локально в защищённом
каталоге `D:\PU-Workspace\private\deploy-artifacts\v3-04a-live-e2e.png`.
