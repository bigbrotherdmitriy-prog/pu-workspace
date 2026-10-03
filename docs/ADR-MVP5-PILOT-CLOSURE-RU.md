# Закрытие пилота MVP-5

Дата решения владельца: 02.10.2026. Статус: пилот закрыт, критерий приёмки V6-07 не выполнен. Закрытие пилота не означает успешной приёмки.

## Решение владельца

Ниже сохранён текст, переданный владельцем в этой задаче 02.10.2026. Это фиксация решения и его оснований, не новая независимая проверка production.

> РЕШЕНИЕ ВЛАДЕЛЬЦА: закрытие пилота MVP-5 (V6-07)
>
> Дата: 02.10.2026
>
> Результат: критерий приёмки не выполнен.
>
> Факт. За период наблюдения 22–29.09.2026 выполнено 0 AUTO-действий при критерии не менее 10 (5 task + 5 notification). Инцидентов не зафиксировано, поскольку ни одно действие не выполнялось.
>
> Причина первая: порог недостижим по построению. context_confidence у всех 85 писем проекта №17 равен 0,55 при требуемых 0,9. Это константа regex-маршрутизатора «проект не определён», а не оценка модели. Значение ≥0,9 достигается только при явном номере договора в тексте, подтверждённом контакте проекта или явной привязке к договору. Ни одно из трёх условий на реальной переписке не выполнялось.
>
> Причина вторая: нет штатного пути восстановления разрешений. Policy revision 4 и authority epoch 2 истекли 25–26.09. Выпуск новых требует действующего authority.manage, который истёк вместе с ними. Механизма первичной выдачи в production-коде не существует: AuthorityResolver.change() (v54_authority.py:145–162) требует уже действующего разрешения и существующей authority-row; mailbox-сервис bootstrap'ом не является. Единственное место, где epoch создаётся с нуля — тестовая фикстура.
>
> Решение. Пилот закрывается с отрицательным результатом. MVP-5 остаётся в состоянии «реализован, в эксплуатацию не принят». AUTO-контур выключен и остаётся выключенным.
>
> Возобновление возможно только при выполнении трёх условий:
>
> 1. Реализован bootstrap-сервис первичной выдачи project authority с тестами и ревью.
> 2. Пересмотрено правило context_confidence: либо снижен порог для узкого класса действий, либо добавлен источник уверенности, достижимый на реальной переписке.
> 3. Появилась практическая потребность в автономных действиях.
>
> V6-08 (MVP-6 группа 3) остаётся заблокированной этим решением.
>
> Внести в ТЗ v6.1: раздел 37 — V6-07 закрыта, результат отрицательный; раздел 41 — MVP-5 «реализован, в эксплуатацию не принят».

## Read-only сверка production перед фиксацией

Проверено 02.10.2026 в 06:03:39 UTC (09:03:39 МСК) на `72.56.108.162`, release `287a64ce4b4d3cfad4355fde64fe2a2f7ad8c922`. Все SQL выполнены с `PGOPTIONS=-c default_transaction_read_only=on`; сам запрос вернул `default_transaction_read_only=on`. Записи в БД и изменения production-конфигурации не выполнялись.

| Проверка | Фактический результат |
|---|---|
| Policy проекта 17 | Последняя revision 4, новый policy ID/revision отсутствует. `valid_until=2026-09-25T04:44:02.328015Z`; срок истёк |
| Authority проекта 17 | Owner user 1, epoch 2, record_version 2. `updated_at=2026-09-24T04:44:02.328015Z`, `valid_until=2026-09-26T04:44:02.328015Z`; срок истёк |
| Аудит новых разрешений после 26.09 | В `v54_audit_extensions` с project_id 17, subject_type policy/authority и связанным audit.created_at ≥ 26.09.2026 00:00 UTC — 0 записей |
| AUTO receipts | В `v54_receipts` с `authorization_origin=SERVER_POLICY`, связанных с `v54_actions.project_id=17`, за всё сохранённое время — 0; task 0, notification 0. Поэтому за окно 22–29.09 тоже 0 |
| Все business actions проекта 17 | В `v54_actions` — 0 записей |
| Mailbox cutover | Для проекта 17 `primary_read=false`, `actions=false` у всех трёх имеющихся cohort/generation-связок |

Есть важное расхождение между решением о выключенном контуре и буквальной конфигурацией: backend, оба worker и scheduler всё ещё содержат `PU_V54_AUTO_PILOT_ENABLED=true`, `PU_V54_AUTO_INTENT_PRODUCER_ENABLED=true`, `PU_V54_AUTO_NOTIFICATION_ENABLED=true`, project_id 17, owner user_id 1. У сохранённой policy также `enabled=true` и task/notification modes AUTO, но её TTL истёк. У одного mailbox cohort producer-флаги `enabled=true`, `pilot_write=true`; флаги доступа `primary_read` и `actions` выключены.

Таким образом, истёкшие policy/authority не дают действующего разрешения на AUTO, и выполненных действий нет, но полное конфигурационное выключение по ENV **не подтверждено**. Это расхождение сообщено владельцу; ADR не выдаёт runtime-флаги `true` за `false`. Флаги не менялись, разрешения не выпускались и TTL не продлевались. Для изменения production-конфигурации требуется отдельное решение владельца.

Проверка истечения срока является fail-closed: [AuthorityResolver.require_principal(), строки 116–119 на production SHA](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/287a64ce4b4d3cfad4355fde64fe2a2f7ad8c922/backend/app/core/v54_authority.py#L116-L119) отклоняет истёкшую authority. [change(), строки 145–162](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/287a64ce4b4d3cfad4355fde64fe2a2f7ad8c922/backend/app/core/v54_authority.py#L145-L162) требует действующего `authority.manage` и существующей строки. Это объясняет отсутствие действующего разрешения, но не заменяет выключение ENV-флагов.

Ключевые SELECT из выполненной проверки приведены ниже. Они запускаются только с `PGOPTIONS='-c default_transaction_read_only=on'`; это воспроизводимые запросы, не инструкция по изменению разрешений.

```sql
SELECT current_setting('default_transaction_read_only');

SELECT id, revision, valid_until, valid_until > now() AS ttl_live
FROM v54_action_policies
WHERE scope_ref::jsonb#>>'{id,value}' = '17'
ORDER BY revision;

SELECT id, principal_id, authority_epoch, record_version, updated_at,
       valid_until, valid_until > now() AS ttl_live
FROM v54_authority_states WHERE project_id = 17 ORDER BY id;

SELECT count(*) AS all_actions
FROM v54_actions WHERE project_id = 17;

SELECT a.action_type, r.outcome, count(*)
FROM v54_receipts r
JOIN v54_actions a
  ON a.id = r.action_id AND a.organization_id = r.organization_id
WHERE a.project_id = 17 AND r.authorization_origin = 'SERVER_POLICY'
GROUP BY a.action_type, r.outcome;

SELECT x.subject_type, x.subject_id, x.sequence, l.id, l.action, l.created_at
FROM v54_audit_extensions x JOIN audit_logs l ON l.id = x.audit_log_id
WHERE x.project_id = 17 AND x.subject_type IN ('policy', 'authority')
  AND l.created_at >= '2026-09-26T00:00:00Z'::timestamptz
ORDER BY l.id;

SELECT c.project_id, c.enabled, f.primary_read, f.actions, f.pilot_write
FROM v54_mailbox_project_cohorts c
JOIN v54_mailbox_cutover_flags f
  ON f.organization_id = c.organization_id
 AND f.mail_connection_id = c.mail_connection_id
 AND f.credential_generation = c.credential_generation
WHERE c.project_id = 17;
```

ENV-флаги сверены через read-only `docker inspect` всех четырёх компонентов. В отчёт выведены только перечисленные выше пять `PU_V54_AUTO_*` ключей и release SHA, без секретов.

## Изменения для ТЗ v6.1

В раздел 37: «V6-07 закрыта с отрицательным результатом; критерий приёмки не выполнен. V6-08 заблокирована решением владельца от 02.10.2026».

В раздел 41: «MVP-5 реализован, в эксплуатацию не принят. За окно 22–29.09.2026 — 0 AUTO-действий. Возобновление возможно только при выполнении трёх условий из ADR-MVP5-PILOT-CLOSURE-RU. Исполнение не авторизовано из-за истёкших policy/authority; полное конфигурационное выключение требует устранить расхождение, зафиксированное read-only сверкой 02.10.2026».

Это подготовленные формулировки для ТЗ, а не утверждение, что внешний файл ТЗ уже изменён. Сам актуальный файл v6.1 не предоставлен для редактирования.

AUTO, выдача authority, продление TTL и возобновление наблюдения этим документом не разрешаются и в рамках его фиксации не выполняются.
