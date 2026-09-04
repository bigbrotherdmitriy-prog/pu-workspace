# Аудит v5.4 provider acceptance harness

## Рамки

- база: `b166dcda862a351861070eed598babf83c65a3f3`;
- ветка: `codex/v54-provider-acceptance-harness`;
- отдельная чистая worktree;
- изменения ограничены четырьмя новыми разрешёнными файлами;
- product code, модели, API, jobs, миграции, frontend, workflows и существующие fixtures не менялись;
- внешние сервисы, production-данные и секреты не использовались.

## Реализовано

`backend/tests/support/v54_fake_provider.py` содержит независимый минимальный Protocol, строгий fake provider и тестовый facade. Контракт разделяет:

- ASSIST без provider effect;
- CONFIRM с точной seal/approval binding;
- fail-closed AUTO;
- provider dispatch, safe retry и reconciliation;
- reversible, compensatable и irreversible действия;
- business outcome и технический timeout;
- exact mailbox scope и provider object identity.

Состояние context correction хранится revision history. Fake provider сохраняет только opaque IDs, хэш payload и безопасные счётчики. Timeout-before-effect фиксируется как `NOT_APPLIED + retry_safe`; timeout-after-effect — как `UNKNOWN`, который блокирует слепой повтор до lookup.

## Покрытие приёмки

| Требование | Проверка |
|---|---|
| A: evidence/context → approved Task | `test_a_message_attachment_evidence_context_to_approved_internal_task` |
| B: повтор без дубля | `test_b_repeat_delivery_and_command_do_not_duplicate_effect` |
| C: correction сохраняет историю | `test_c_context_correction_preserves_project_and_contract_history` |
| D: high-risk без approval | `test_d_assist_has_no_effect_auto_denied_and_high_risk_requires_approval` |
| E: payload mutation | `test_e_changed_payload_invalidates_approval_and_conflicts_with_bound_command` |
| F: send UNKNOWN | `test_f_timeout_after_effect_stays_unknown_until_scoped_reconciliation` |
| G: отдельный corrective follow-up | `test_g_irreversible_send_cannot_rollback_and_follow_up_is_separate_action` |
| H: изоляция mailbox | `test_h_same_provider_object_id_is_strictly_scoped_to_exact_mailbox` |

Дополнительные тесты проверяют explicit retry после timeout-before-effect, stale authority/capability/credentials, unknown mailbox, safe journal, отдельное cancel action и запрет прямого rollback compensatable action.

## Результаты проверок

| Проверка | Результат |
|---|---|
| Новые provider acceptance tests | `14 passed` |
| Новые tests + существующий v5.4 CONFIRM corpus subset | `18 passed` |
| Структурный валидатор v5.4 corpus | `PASS`: 28 cases, 14 assets, 52 excerpts |
| Полный backend regression, первый прогон | `768 passed, 9 skipped`, 4 Alembic deprecation warnings |
| Полный backend regression, контрольный прогон | `767 passed, 9 skipped, 1 failed`: существующий performance smoke занял 10.57 с при лимите 10 с |
| Изолированный повтор performance smoke | `1 passed` за 2.66 с; сбой классифицирован как timing/environment fluctuation, не скрыт |
| `git diff --check` | PASS |

Девять существующих skip не превращались в PASS и не изменялись. Performance-тест и его порог не изменялись: первый полный прогон прошёл, контрольный превысил порог на 0.57 с, а немедленный изолированный повтор прошёл. Structural corpus validator отдельно сообщает `application=NOT_RUN` и `postgres_fault_tests=NOT_RUN`; это сохранено как ограничение, а не как успешная продуктовая проверка.

## Статус доказательства

- **HARNESS CONTRACT PASS** — 14/14 новых контрактных тестов проходят;
- **PRODUCT INTEGRATION NOT RUN** — runtime PU Workspace намеренно не импортирован;
- **LIVE PROVIDER NOT RUN** — сетевые провайдеры намеренно не вызывались.

## Ограничения и запрос интегратору

Harness не доказывает транзакционность product DB/outbox, работу очереди, конкурентный recovery, реальные permissions/policy или поведение API провайдера. Интегратор должен реализовать один adapter к существующему action pipeline по точному интерфейсу из `docs/acceptance/v54-provider-harness/README_RU.md`, затем прогнать те же сценарии на продуктовой композиции и отдельные live-provider contract tests в изолированной тестовой учётной записи.

## Исправления после независимого review

Отдельный follow-up усиливает именно контракт harness, не повышая статус до
product PASS:

- idempotency key теперь связан со всем immutable `ProviderRequest`, а не
  только с payload hash;
- replay с другим action ID/revision при том же command key запрещён;
- reconciliation повторно проверяет project, mailbox, authority, capability и
  credential generation, а найденный receipt обязан точно совпасть с UNKNOWN;
- rollback разрешён только для существующего exact `APPLIED` reversible action
  с действующим exact approval;
- corrective follow-up требует отдельного approval и точного ранее применённого
  irreversible send в том же mailbox/project;
- добавлены негативные сценарии forged action, stale live state, rollback без
  эффекта и invalid corrective target.

В локальном окружении follow-up выполнена Python compilation и
`git diff --check`. Повтор pytest требует среды с установленным pytest и должен
быть выполнен интеграционным CI; до этого итог follow-up остаётся
**CONDITIONAL**, а не заменяется статической проверкой.
