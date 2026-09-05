# MVP4 — исполняемая приёмка целостности финансов и ГПР

Этот пакет закрывает критерии MVP4, которые намеренно не входили в pilot v5.4.
Источником истины служат тесты `backend/tests/test_mvp4_finance_integrity.py` и
`backend/tests/test_mvp4_cpm_acceptance.py`.

| ID | Инвариант | Доказательство |
|---|---|---|
| F01 | Суммы приводятся к Decimal(18,2) правилом HALF_UP; неконечные, отрицательные и выходящие за границы значения отклоняются | `test_money_is_half_up_bounded_and_currency_is_strict` |
| F02 | При нескольких валютах общий денежный итог не вычисляется; API возвращает независимые итоги по валютам | `test_mixed_currency_overview_never_returns_a_cross_currency_total` |
| F03 | Подтверждение, коррекция и сторно добавляют события, не изменяя предшествующие события | `test_payment_confirmation_correction_and_reversal_are_append_only` |
| F04 | Изменение версии или хэша документа после предложения останавливает платёж с HTTP 409 | `test_source_version_pin_rejects_stale_payment` |
| F05 | Задача счёта обязана принадлежать тому же проекту | `test_task_link_must_belong_to_same_project` |
| G01 | Backend вычисляет critical path и total float для FS/SS/FF/SF и lag | `test_backend_cpm_calculates_critical_path_and_float_for_typed_links` |
| G02 | Верхние ограничения SNLT/FNLT дают явное нарушение и отрицательный резерв, ALAP вычисляется backward-pass | `test_backend_cpm_supports_sf_and_upper_bound_constraints`, `test_alap_uses_backward_pass_without_mutating_baseline_dates` |

Обязательный PostgreSQL gate перед выпуском: `alembic upgrade head`, повторный
`upgrade` на актуальной production-like копии, затем параллельная отправка двух
платёжных команд с одинаковым и различным idempotency key. Допустим ровно один
event для одинакового ключа; различный payload с тем же ключом обязан вернуть 409.
