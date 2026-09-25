# V6-00a: безопасное применение Money/project-currency foundation

Эта процедура обязательна перед применением миграции `c70a00a1f001` на production.

## Stop conditions

Деплой останавливается до Alembic upgrade, если:

- backup PostgreSQL не создан или не прошёл штатную проверку;
- dry-run проекта №17 завершился не `PASS`;
- `changed_value_count` не равен нулю;
- `max_abs_delta` не равен `0.00`;
- обнаружена хотя бы одна финансовая строка не в RUB;
- dry-run не перечислил все существующие денежные поля проекта №17.

Расхождение даже на `0.01` — безусловный стоп. Автоматическое исправление или молчаливое округление существующих данных запрещено.

## Порядок

1. Зафиксировать точный deploy SHA.
2. Выполнить стандартный production backup из `scripts/deploy-production.sh` и проверить созданный файл/контрольную сумму.
3. До `alembic upgrade head` запустить candidate-код только для read-only отчёта:

   ```bash
   PYTHONPATH=/workspace/backend python /workspace/backend/scripts/v6_00a_money_dry_run.py \
     --project-id 17 \
     --expected-currency RUB \
     --output /secure-validation/v6-00a-project-17-money-dry-run.json
   ```

4. Проверить в JSON: `status=PASS`, `changed_value_count=0`, `max_abs_delta=0.00`, `currency_mismatch_count=0`.
5. Только после этого применить миграцию стандартной процедурой.
6. После миграции повторить read-only отчёт и сравнить строки `before/minor_units/after` с pre-migration отчётом.

Отчёт содержит только идентификаторы строк и денежные значения; имена контрагентов, тексты документов и секреты в него не входят. Отчёт не коммитится в Git.

## Этап 2: Snapshot API (`c70a00a2f001`)

1. Зафиксировать merge SHA и предыдущий production SHA. Создать `pg_dump -Fc`
   и проверить восстановлением в изолированном контейнере без сети.
2. Сохранить отсортированную CSV-выгрузку всех денежных полей проекта №17.
3. Запускать `scripts/deploy-primary-first-host.sh` **из каталога точного release**,
   не из `/root`. Перед сменой пользователя через `runuser` выполнить `cd` в release:
   `runuser` может унаследовать недоступный рабочий каталог `/root`.
4. Применить миграцию, синхронизировать backend, оба worker и scheduler на merge SHA.
5. Повторить ту же CSV-выгрузку. Требуется побайтное совпадение; при расхождении
   остановить приёмку и выполнить согласованный rollback, не исправлять суммы.
6. Проверить schema=`c70a00a2f001`, readiness, Gmail auto-sync и четыре SHA.
7. Read-only запросами `/execution/cash-flow/views` проверить инварианты и стабильность
   revision/hash. Сопоставить с четырьмя текущими вкладками UI при одинаковом периоде.
   Legacy UI до этапа 3 считает суммы в браузере; расхождения фиксировать таблицей,
   не исправлять в рамках деплоя этапа 2.
