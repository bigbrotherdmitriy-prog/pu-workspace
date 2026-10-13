# Статус ТЗ PU Workspace v6.0 — на 13.10.2026

Сверка каждого проверяемого пункта ТЗ с кодом, актами и прод-данными. Первичную сверку сделал Gemini по тексту ТЗ и репозиторию; строки «не сделано» и все изменения 13.10.2026 проверены Claude вручную по коду и проду.

## Итог

- **Закрыто кодом и/или актами:** все пункты MVP-1…MVP-7 в части, проверяемой без внешних учётных данных; V6-00…V6-06, V6-09; push-уведомления (V6-03, V6-04) — доставка на реальный телефон подтверждена 13.10.2026.
- **Перенесено решением владельца:** V6-07 (AUTO-пилот почты) — в этап «Переход почты на основное чтение» (ADR-V6-07, дополнение 13.10.2026).
- **Ждёт владельца:** живая проверка MVP-1 на тестовых аккаунтах Google Диска / Яндекс Диска (и S10 live provider); живая приёмка регламента MVP-7 (первый реальный регламент); открытие карточки задачи по push — на первом реальном назначении.
- **Вне объёма по самому ТЗ:** прогноз денежных разрывов, коммерческая эксплуатация, расширенные агенты (V6-08).

Сделано 13.10.2026 сверх ТЗ по итогам эксплуатации: мобильный режим веб-приложения (PR #161, #163–#165), доступ к почте на 7 дней с напоминаниями (#152), сигнал «почта не загружается» (#153), одно письмо — один проект (#154), сроки «до 16.10» без года (#162), выкладка обновляет воркеры (#151).

| Раздел | Требование (коротко) | Статус | Доказательство | Осталось сделать | Оценка, ч |
|---|---|---|---|---|---|
| Разд. 24 | MVP-1: Google Drive подключение, snapshot, virtual tree без обязательной копии | СДЕЛАНО | Акт `docs/acceptance/mvp1-project-launch/CLOSURE_ACT_RU.md`; коммит `feat(mvp1): add Snapshot metadata envelope, shortcut support and pagination` (#20); `test_mvp1_acceptance.py` | — | — |
| Разд. 24 | MVP-1: dry-run, предложения по структуре и имени, неизменность Drive до подтверждения | СДЕЛАНО | Акт `docs/acceptance/mvp1-project-launch/CLOSURE_ACT_RU.md`; `test_bulk_standardization.py` (`test_standard_name_is_idempotent...`) | — | — |
| Разд. 24 | MVP-1: проверка актуальности, conflict_source_changed, защита удаления договоров | СДЕЛАНО | `test_contract_update_accepts_commercial_fields_and_delete_requires_confirmation`; `test_physical_contract_delete_is_blocked...` | — | — |
| Разд. 24 | MVP-1: идемпотентность операций, прозрачный сбой, rollback | СДЕЛАНО | Акт `docs/acceptance/mvp1-project-launch/CLOSURE_ACT_RU.md`; `test_bulk_standardization_changes_safe_copy_and_logs_rollback_data` | — | — |
| Разд. 24 | MVP-1: аудит существенных действий, блокировка внешнего AI политикой | СДЕЛАНО | `test_ai_policy.py`; `models/audit_log.py`; коммит `feat(mvp1): separate snapshots from mutations and protect contracts` | — | — |
| Разд. 24 | MVP-1: Живая OAuth-приёмка Google Drive / Яндекс Диск на реальных тестовых аккаунтах | НУЖЕН ВЛАДЕЛЕЦ | Акт `CLOSURE_ACT_RU.md` (раздел «Явно не входит в этот акт», 18 подключений только Google, Yandex не проверен на проде) | Предоставить тестовые учётные записи Google/Яндекс и выполнить живую OAuth-верификацию | — |
| Разд. 25 | MVP-2: приём сообщений/документов, связь с первоисточником | СДЕЛАНО | Коммиты PR #23, PR #32; `test_mvp2_message_workflow.py`; модули `automations/gmail.py`, `document_extraction.py` | — | — |
| Разд. 25 | MVP-2: краткая справка, определение проекта/договора, подтверждение при низкой уверенности | СДЕЛАНО | Коммиты PR #23, PR #64; `test_gemini_analysis.py`; `summary_engine.py`; `owner_context_confirmation.py` | — | — |
| Разд. 25 | MVP-2: извлечение поручений, сроков, ответственных, сумм с сохранением основания | СДЕЛАНО | Коммит `feat(mvp2): expose task amount/assignee-hint, extend regex fallback` (#145); `test_document_extraction.py` | — | — |
| Разд. 25 | MVP-2: редактируемый проект ответа без автоматической отправки | СДЕЛАНО | `models/response_draft.py`; `test_response_drafts_api.py`; PR #29 `fix(mail): require manager role to approve mail drafts` | — | — |
| Разд. 25 | MVP-2: создание Tasks/Calendar через durable outbox после подтверждения, idempotency | СДЕЛАНО | `test_mvp2_provider_outbox.py`; `test_google_tasks.py`; `test_google_calendar.py`; PR #32, PR #35 | — | — |
| Разд. 25 | MVP-2: отказ AI не теряет исходное сообщение и допускает ручную обработку | СДЕЛАНО | `test_document_extraction.py`; модуль `document_extraction.py` (fallback worker pool) | — | — |
| Разд. 26 | Telegram: текстовый/файловый приём, сводки, ссылки, отсутствие обходных прав | СДЕЛАНО | `test_telegram_transport.py`; `test_telegram_files.py`; `test_telegram_task_commands.py`; `integrations/telegram.py` | — | — |
| Разд. 26 | Telegram: устойчивость polling и мониторинг здоровья релея | СДЕЛАНО | Коммит `fix(telegram): recover polling and report stale relay health` (PR #90); `test_telegram_relay_health.py` | — | — |
| Разд. 27 | Финансы: ГПР, версии базового плана, критический путь (CPM), расчет float | СДЕЛАНО | Акт `docs/acceptance/mvp4-finance-hardening/README_RU.md`; `test_backend_cpm_calculates_critical_path_and_float_for_typed_links` | — | — |
| Разд. 27 | Финансы: импорт MPP (MPXJ 16) и Excel ДДС с preview и отменой операции | СДЕЛАНО | Коммиты PR #76, PR #78, PR #80, PR #107; `test_mpp_schedule_import.py`; `schedule_import/mpp.py` | — | — |
| Разд. 27 | Финансы: прогноз денежных разрывов | ВНЕ ОБЪЁМА | ТЗ разд. 32, 43 (прямо объявлен out-of-scope до отдельного решения владельца и стабилизации V6-00) | Реализация прогноза кассовых разрывов | — |
| Разд. 27.1 | Инвариант: цепочка Договор → Бюджет + ГПР → Счёт → ДДС → Платёж; Акт → actual | СДЕЛАНО | Акт `CLOSURE_ACT_V6-00_RU.md`; PR #51, PR #125; `test_mvp4_canonical_finance_chain.py` | — | — |
| Разд. 27.1 | Инвариант: committed и actual — разные проекции и не складываются | СДЕЛАНО | PR #49; Акт `CLOSURE_ACT_V6-00_RU.md`; `test_mvp4_act_budget_actual.py` | — | — |
| Разд. 27.1 | Инвариант: серверная валидация тройки «договор + этап ГПР + строка бюджета» во всех путях | СДЕЛАНО | Акт `CLOSURE_ACT_V6-00_RU.md` (п. 3, 5); `test_mvp4_canonical_finance_chain.py`; `dds_budget_links.py` | — | — |
| Разд. 27.1 | Инвариант: фиксация hash/version источника документа; сторно/компенсация | СДЕЛАНО | PR #48, PR #131; `test_mvp4_finance_source_pins.py`; `test_payment_confirmation_correction_and_reversal_are_append_only` | — | — |
| Разд. 27.1 | Инвариант: человек всегда подтверждает категорию затрат, автоприменения нет | СДЕЛАНО | PR #33 `feat(finance): add human-confirmed invoice cost classification`; `test_invoice_cost_classification.py` | — | — |
| Разд. 27.1 | Инвариант: открытый список категорий затрат; разделение направлений (доход/расход) | СДЕЛАНО | PR #125, PR #127; `test_v605_income_budget_direction.py`; `test_dds_article_budget.py` | — | — |
| Разд. 31 | MVP5 Сценарий A: входящее письмо с вложением → Context Graph → proposal → approval → task/reply | СДЕЛАНО | Коммиты PR #52, PR #67; `test_mvp5_pilot_acceptance.py`; `test_v54_context_communication.py` | — | — |
| Разд. 31 | MVP5 Сценарий B: дедупликация входящего сообщения (нет дублей задач/действий) | СДЕЛАНО | `test_idempotency.py`; `test_v54_provider_acceptance_contract.py` (сценарий B) | — | — |
| Разд. 31 | MVP5 Сценарий C: исправление пользователем контекста фиксируется в аудите | СДЕЛАНО | Коммит PR #64; `test_mvp5_owner_context_confirmation.py`; `test_v54_provider_acceptance_contract.py` | — | — |
| Разд. 31 | MVP5 Сценарий D: блокировка high-risk действий без approval через policy gate | СДЕЛАНО | `test_v54_autonomy_authorization.py`; `test_v54_action_trust.py`; `action_trust/guards.py` | — | — |
| Разд. 31 | MVP5 Сценарий E: изменение payload аннулирует выданное ранее approval | СДЕЛАНО | `test_v54_provider_acceptance_contract.py` (сценарий E); `core/v54_authority.py` | — | — |
| Разд. 31 | MVP5 Сценарий F: federated source reference без локальной копии при запрете | СДЕЛАНО | Документ `docs/acceptance/v54-corpus/README_RU.md` (P03); `staging/contracts.py` | — | — |
| Разд. 31 | MVP5 Сценарий G: маркировка недоступного/устаревшего provider source | СДЕЛАНО | `test_readiness_diagnostics_edges.py`; коммит `fix(mail): keep cached mailbox usable during provider outage` | — | — |
| Разд. 31 | MVP5 Live Provider Sandbox S10: реальное исполнение против внешнего провайдера | ЧАСТИЧНО | Документ `docs/acceptance/v54-live-provider-sandbox/README_RU.md` (статус NOT_RUN без секретов); `v54_live_provider_bridge.py` | Подключение live sandbox environment в GitHub Actions и прогон с реальным API | 8 |
| Разд. 31 | v5.5 Сценарий A/B: Finalized-State Guard блокирует изменение закрытых объектов | СДЕЛАНО | `action_trust/guards.py`; `action_trust/state.py`; `test_v54_action_trust.py` | — | — |
| Разд. 31 | v5.5 Сценарий C: разделение классов знаний (Project, Policy, Vetted, Model) | СДЕЛАНО | `summary_engine.py`; `source_evidence/facade.py`; `test_v54_source_evidence_pilot.py` | — | — |
| Разд. 31 | v5.5 Сценарий D: Evidence Gates (отсутствие evidence блокирует переход) | СДЕЛАНО | `action_trust/validation.py`; `test_v54_action_trust_support.py` | — | — |
| Разд. 31 | v5.5 Сценарий E: Boundary-Aware Execution (AI сообщает об ограничениях) | СДЕЛАНО | `gemini_analysis.py`; `test_gemini_analysis.py`; `test_v54_c01_content_pipeline.py` | — | — |
| Разд. 31 | v5.5 Сценарий F / Разд. 41: Offline Evidence Queue (офлайн-очередь заметок/фото) | СДЕЛАНО | Коммиты PR #68, PR #73; `test_mobile_sync.py`; `test_android_mobile_upload.py`; `api/mobile_sync.py` | — | — |
| Разд. 31 | v5.6 Сценарий A: Agent Preflight / Simulation (симуляция до включения) | СДЕЛАНО | Коммит PR #69; `api/autonomy_readiness.py`; `test_v54_autonomy_readiness.py` | — | — |
| Разд. 31 | v5.6 Сценарий B: Runtime Permission & Policy Revalidation при каждом запуске | СДЕЛАНО | Коммиты PR #135, PR #138; `core/v54_permissions.py`; `test_mailbox_authority_renewal.py` | — | — |
| Разд. 31 | v5.6 Сценарии C/D/E: Cross-Evidence Reconciliation (сопоставление разнородных данных) | СДЕЛАНО | Коммит PR #71; `api/evidence_trail.py`; `test_evidence_trail_api.py` | — | — |
| Разд. 31 | v5.6 Сценарий F: Workflow Template Agents / Регламенты исполнения (MVP-7) | СДЕЛАНО (код и тесты) | `automation_engine.py`; PR #149 — поведенческие тесты всех 9 критериев MVP-7 в main; акт `docs/acceptance/mvp7-ai-secretary-control/CLOSURE_ACT_RU.md` | Живая приёмка на первом реальном регламенте (ждёт владельца) | — |
| Разд. 35 | AI Quality Gate: привязка к проекту с confidence; извлечение без выдумок дат/сумм | СДЕЛАНО | PR #126, PR #145; `test_document_extraction.py`; `test_contract_vat_finance.py` | — | — |
| Разд. 35 | AI Quality Gate: повтор анализа без повторной загрузки документа | СДЕЛАНО | Коммит PR #47 `feat(mvp4): retry invoice AI analysis without reupload`; `test_document_extraction.py` | — | — |
| Разд. 35 | AI Quality Gate: сохранение модели, версии промпта, confidence и подтверждения | СДЕЛАНО | Модели `models/ai_cache.py`, `models/document.py`; `test_ai_cache.py` | — | — |
| Разд. 36 | API Outbox Contract: at-least-once доставка, UNKNOWN запрещает слепой повтор | СДЕЛАНО | Коммит PR #35; `test_mvp2_provider_outbox.py`; `core/external_retry.py` | — | — |
| Разд. 36 | API Finance Chain Contract: единый валидатор тройки contract + schedule + budget | СДЕЛАНО | Коммит PR #51; `test_mvp4_canonical_finance_chain.py`; `dds_budget_links.py` | — | — |
| Разд. 36 | API Import Contract: preview → редактируемое подтверждение → apply → отмена | СДЕЛАНО | Коммит PR #78 `feat: dds-cancel-operation`, PR #80; `test_structured_import.py` | — | — |
| Разд. 36 | API Notification Channel Contract: единый механизм нотификаций для Telegram/FCM | СДЕЛАНО | Коммит PR #55; `core/notifications.py`; `test_notifications.py` | — | — |
| Разд. 37 | Backlog V6-00: Стабилизация ДДС, ГПР и внесения счетов на реальном проекте | СДЕЛАНО | Акт `docs/acceptance/mvp4-finance-hardening/CLOSURE_ACT_V6-00_RU.md` от 07.10.2026 (коммит #137) | — | — |
| Разд. 37 | Backlog V6-01: Независимая сверка отчёта MVP-6 (безопасная часть) | СДЕЛАНО | Акт `docs/acceptance/mvp6-document-control/CLOSURE_ACT_RU.md` от 09.10.2026 (коммит #146) | — | — |
| Разд. 37 | Backlog V6-02: Android: отображение приоритета, описания, источника, участников | СДЕЛАНО | Коммит PR #73 `feat(android): show task and document details`; `test_android_native_wrapper.py` | — | — |
| Разд. 37 | Backlog V6-03: FCM preflight + план реализации push-уведомлений | СДЕЛАНО | Firebase-проект владельца pu-workspace-57c66; план `docs/architecture/V6-03-FCM-PUSH-PLAN-RU.md` утверждён владельцем 13.10.2026 (PR #156) | — | — |
| Разд. 37 | Backlog V6-04: FCM push по требованиям раздела 42 (durable outbox, безопасность PII) | СДЕЛАНО | PR #157–#160 (токены устройств, канал FCM HTTP v1 с durable outbox, Android-клиент, монтирование ключа read-only); APK 1.1.0 подписан и установлен; 13.10.2026 push доставлен на телефон владельца (Android 17), клик открыл экран уведомлений проекта | Проверить открытие карточки задачи по push на первом реальном назначении | — |
| Разд. 37 | Backlog V6-05: Разделение доходных и расходных бюджетов | СДЕЛАНО | Коммиты PR #125, PR #127, PR #129; `test_v605_income_budget_direction.py` | — | — |
| Разд. 37 | Backlog V6-06: schedule_budget_links — бюджет по этапам ГПР «многие ко многим» | СДЕЛАНО | Коммит PR #125; `test_gpr_schedule_budget_links.py`; `dds_budget_links.py` | — | — |
| Разд. 37 | Backlog V6-07: Итоги пилота MVP-5 (≥10 AUTO, 0 инцидентов, ≥7 дней) | ПЕРЕНЕСЕНО | Решение владельца «Б» 13.10.2026: AUTO для почты структурно недостижим — источники Gmail не переводятся в `current`, cutover `primary_read` выключен (ADR-V6-07, дополнение 13.10, PR #166) | Следующий этап «Переход почты на основное чтение»: ADR + 3–5 дней + 7 дней наблюдения | 24–40 |
| Разд. 37 | Backlog V6-08: MVP-6 группа 3 — расширенные агенты | ВНЕ ОБЪЁМА | ТЗ разд. 32, 37 (зависит от закрытия пилота V6-07 и утверждения отдельного ADR) | Разработка кастомных агентов по новому ADR | — |
| Разд. 37 | Backlog V6-09: Закрыть staging: удалить deploy-staging из CI и env | СДЕЛАНО | Коммит PR #82 `ci: retire public staging deployment`; `docs/architecture/ADR-V6-09-DUAL-GMAIL-SYNC-RU.md` | — | — |
| Разд. 39 | Stop Conditions: контроль утечки секретов и содержимого в логи/пуши | СДЕЛАНО | `test_security.py`; `docs/acceptance/v54-corpus/README_RU.md`; `test_v54_staging_safety_hardening.py` | — | — |
| Разд. 39 | Stop Conditions: запрет слепого повтора при UNKNOWN в outbox | СДЕЛАНО | `core/external_retry.py`; `provider_actions/recovery_guards.py`; `test_mvp2_provider_outbox.py` | — | — |
| Разд. 39 | Stop Conditions: обратимость миграций схемы БД (rollback Alembic) | СДЕЛАНО | `test_migration_clean_postgres.py`; `test_schema_revision.py` | — | — |
| Разд. 41 | Фактическое состояние: управление задачами/историей MVP-3 (инварианты CAS) | СДЕЛАНО | Акт `docs/acceptance/mvp3-management-history/CLOSURE_ACT_RU.md` от 09.10.2026 (коммит #144) | — | — |
| Разд. 42 | Android и push: TWA клиент, offline queue и фотофиксация | СДЕЛАНО | Коммиты PR #68, PR #73; `test_android_pwa.py`; `test_android_mobile_upload.py` | — | — |
| Разд. 42 | Android и push: регистрация токена FCM, отсутствие сумм/PII в payload | СДЕЛАНО | PR #157 (регистрация/отзыв токенов), #158 (payload только заголовок+ссылка, тест), #159 (Android FirebaseMessagingService, POST_NOTIFICATIONS, puworkspace.ru); токен телефона владельца зарегистрирован 13.10 | — | — |
| Разд. 43 | Решения владельца: коммерческая готовность и внешние заказчики | ВНЕ ОБЪЁМА | ТЗ разд. 43 (прямо зафиксировано решение: пока out-of-scope, возврат после стабилизации) | Подготовка к внешней коммерческой эксплуатации | — |
