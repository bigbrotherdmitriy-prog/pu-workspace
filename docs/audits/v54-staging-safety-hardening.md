# V5.4 staging safety hardening

Дата: 2026-09-04

Ветка: `codex/v54-staging-safety-hardening`

База: `1310f8a6a25e4f0a547b8ca7bce5c5a4548b94be`

## Решение

Закрыты два P1 из `v54-wave3-security-review.md`, при этом local-upload rollout
и production composition не включались.

1. Добавлен отдельный server-injected `LocalUploadRetentionAuthority`. Он имеет
   allowlist точных `(organization_id, project_id)`, residency и KEK, не вызывает
   user policy и не использует бывшего владельца как service actor.
2. Scheduler вызывает bounded recovery. Без установленного local-upload runtime
   вызов является безопасным no-op.
3. Failed/dead-letter объект проходит crash-safe порядок: durable `EXPIRED`,
   идемпотентное удаление ciphertext, durable `PURGED`. Ошибка удаления оставляет
   объект в `EXPIRED`; следующий проход продолжает очистку.
4. Service audit остаётся в существующем AuditLog/AuditExtension ledger. Для него
   добавлен взаимно исключающий origin `service_principal`; `actor_id` не подменяется.
   Details, исключения, filename, locator, checksum и content не записываются.
5. Каждый `flush`/`commit` legacy local-upload processor теперь удерживает
   `FOR UPDATE` на exact BackgroundJob claim и проверяет `(job_id, worker_id,
   attempts, locked_at, lease, cancelled_at)` в той же транзакции.
6. Partial unique DB index на `(project_id, external_id)` только для
   `source='local_upload'` не допускает второй Document для stable staging source.
   `index_documents` также учитывает provider/source при поиске существующей записи.

## Миграция

- Единственная новая последовательная ревизия: `a54f001c0a08`.
- `down_revision`: `a54f001c0a07`.
- Единственная head: `a54f001c0a08`.
- Upgrade не удаляет и не объединяет документы. Если уже существуют дубли
  local-upload identity, upgrade fail closed с требованием ручного разбора.
- Downgrade запрещён при наличии service audit records, чтобы не потерять
  происхождение retention-действий.
- Offline PostgreSQL SQL render: PASS.

## Регрессии

Добавлены синтетические тесты для:

- потери lease непосредственно перед legacy commit;
- DB-запрета duplicate local Document;
- изоляции одинакового external id между local upload и другим provider;
- dead-letter purge после revoke/недоступности user authority;
- crash после durable EXPIRED и повторного delete;
- отказа service authority для неразрешённого project scope;
- единственной Alembic head и offline a08 SQL;
- opt-in PostgreSQL inspection индекса, nullable service actor и actor-origin check.

До реализации новый набор не собирался из-за отсутствия retention recovery API;
это фиксирует исходный P1. После реализации:

- staging-safety regression: `7 passed, 1 PostgreSQL skip`;
- расширенный local/Gmail/materialization/schema target: `169 passed, 6 skipped`;
- CI scripts: `107 passed`, три существующих Windows path-encoding failure при
  запуске из пути с кириллицей; изменения staging их не затрагивают;
- Alembic heads: `a54f001c0a08 (head)`;
- `git diff --check`: PASS.

Полный backend был остановлен интеграционным координатором на 57% без ошибок,
чтобы не дублировать единый финальный прогон. PostgreSQL runtime не заявляется
PASS локально; opt-in тест включён в `v54_pilot_workflow.py`.

## Остаточные условия перед rollout

- Требуется PostgreSQL CI на чистой БД до `a54f001c0a08`.
- Production composition должна явно установить retention authority с точными
  scopes/residency/KEK; в этом коммите она намеренно не устанавливается.
- Перед migration upgrade оператор должен проверить отсутствие historical
  duplicate local-upload documents. Автоматического удаления данных нет.
- Реальные документы, provider API, OAuth, production secrets и production data
  не использовались.
