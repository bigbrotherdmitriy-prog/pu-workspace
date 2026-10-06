# PU Workspace — общий read-only аудит

Дата проверки: **2026-09-24**
Репозиторий: `bigbrotherdmitriy-prog/pu-workspace`
Проверенный ref: `origin/main`
HEAD: **`5300d1cee86f740d9e846615924145b020bc0721`**
Последний merge: **2026-09-24 14:33:15 +0300**, PR #81.

> Проведён внешним агентом (ChatGPT/Codex). Ключевые количественные утверждения
> независимо перепроверены во второй сессии (Claude) — см. §12.

## 1. Итог

`main` собирается, frontend и основной backend regression зелёные. MVP1–MVP4 интегрированы. Все семь PR MVP5 смержены, но утверждение «MVP5 завершён» корректно только в смысле **code integration**: живое 7-дневное окно AUTO-пилота и минимальная выборка 10 действий ещё не могут считаться закрытыми репозиторием.

Аудит обнаружил один воспроизводимый PostgreSQL-дефект, который скрыт стандартным skip:

- `test_v54_authority_postgres.py::test_postgres_role_change_linearizes_before_dispatch_check` падает отдельно на актуальном `main` с `ValueError: authority_epoch_required`; второй поток затем завершается по таймауту. Повтор в чистом процессе дал тот же результат.

Также обнаружены два дефекта переносимости тестового стенда:

- `docker-compose.ci.yml` создаёт БД `pu_test`, тогда как четыре migration/concurrency-теста разрешают generic fallback только для `pu_workspace_test`;
- `Dockerfile.ci` не копирует корневые `scripts/` и `docs/`, поэтому образ не является автономным полным test runner без read-only mount checkout.

### Приоритет

| Уровень | Что делать |
|---|---|
| **Критично** | Исправить authority epoch update/guard и включить этот PostgreSQL test в обязательный CI gate. |
| **Критично** | Согласовать имя БД в `docker-compose.ci.yml` с тестовыми guard либо сделать guard параметризованным; сделать CI-образ самодостаточным. |
| **Стоит сделать** | Не называть MVP5 live-complete до завершения 7 дней + 10 действий (5+5) без инцидентов; хранить итоговый протокол отдельно от code-complete. |
| **Стоит сделать** | Разобрать 25 несмерженных веток, закрыть/обновить PR #19, удалить после подтверждения 83 уже интегрированных remote-ref. |
| **Стоит сделать** | Обновить README и индекс audit-документов; устаревшие `NOT RUN/BLOCKED/PROPOSED` сейчас выглядят как текущий статус. |
| **Можно отложить** | Унификация pagination, удаление неиспользуемых synchronous provider wrappers, major dependency upgrades и code splitting frontend bundle. |

## 2. Последние PR и открытые PR

Последние 10 merge по публичному GitHub API:

| PR | Merge commit | Суть |
|---:|---|---|
| #81 | `5300d1c` | planned DDS import виден непосредственно в DDS workspace |
| #80 | `88280e7` | monthly DDS import, editable review, MPP cash-flow proposals |
| #79 | `3cc1b4d` | читаемые light/dark темы на workspace pages |
| #78 | `b743e40` | DDS cancellation, bulk invoice upload, rotating project HQ |
| #77 | `1f6b1a2` | fullscreen GPR/DDS workspace |
| #76 | `f9a0f6a` | MPP import с MPXJ 16 |
| #74 | `cd0dde3` | fullscreen GPR/DDS workbook |
| #73 | `c7f5b7a` | Android task/document detail screens |
| #72 | `6f7e665` | unified GPR and DDS planning workspace |
| #71 | `101c2e7` | MVP6 project-scoped cross-evidence trail |

Открыт один PR: **#19**, `codex/pu-39-staging-runbook`, «[PU-39] Уточнить диагностику staging после перевода EU в production», последнее обновление 2026-09-08. Это не код продукта, а stale runbook PR; требуется решение владельца — обновить или закрыть.

## 3. Статус MVP-волн

| Волна | Проверка merge/code | Вердикт |
|---|---|---|
| MVP1 | PR #20 и ветка `mvp1-main-integration` в `main`; OAuth/snapshots/OCR/storage/app/Alembic присутствуют | **CODE DONE**. `mvp1-phase2-review` не ancestor и без PR; это документационный хвост, а не недостающий runtime-код. |
| MVP2 | PR #23, #30, #31, #32, #35 смержены; активные Gmail/Tasks/Calendar mutations идут через durable provider action queue | **CODE DONE**, live/provider reconciliation имеет документированные эксплуатационные ограничения. |
| MVP3 | PR #37, #39, #41, #42, #45 смержены; PostgreSQL suite в этом аудите: 18 PASS | **DONE** по заявленному scope. |
| MVP4 | PR #47–#51 смержены; finance pin migration gate PASS | **DONE** по заявленному scope. Будущая many-to-many связь schedule↔budget остаётся отдельным ADR. |
| MVP5 | PR #52, #53, #56, #57, #60, #64, #67 смержены | **CODE INTEGRATED / LIVE ACCEPTANCE OPEN**. AUTO-пилот активирован только 2026-09-22; 7-дневный критерий физически ещё не закрыт на дату аудита. |
| MVP6 | PR #69 preflight panel и #71 cross-evidence смержены | **STARTED / NOT CLOSED**: отдельной closure-волны нет. |

### MVP5: точная проверка веток

`git merge-base --is-ancestor` даёт ancestor для пяти веток. Две ветки не являются ancestors из-за squash merge, но их содержимое присутствует в соответствующих merge commits:

| PR / ветка | Строгий ancestor | Фактический статус |
|---|---:|---|
| #52 `mvp5-auto-product-composition` | нет | squash merge `4e3cfec`; `git cherry` помечает tip как patch-equivalent (`-`) |
| #53 `mvp5-s10-live-bridge` | нет | squash merge `7e9f864`; diff по шести файлам реализации между merge commit и branch tip пуст |
| #56 `mvp5-auto-incident-alert` | да | merged |
| #57 `mvp5-project16-shadow-prep` | да | merged |
| #60 `mvp5-project17-cohort` | да | merged |
| #64 `mvp5-owner-context-confirmation` | да | merged |
| #67 `mvp5-inbound-auto-producer` | да | merged |

Следовательно, забытых MVP5 code-веток нет. Расхождение относится к Git topology и к незавершённой live-приёмке, а не к отсутствующему коду.

## 4. Тесты

### Основной regression

| Проверка | Результат |
|---|---|
| Exact-tree GitHub backend CI | **2025 passed, 59 skipped, 47 warnings** |
| Локальный полный Docker/PostgreSQL run с `PU_TEST_POSTGRES=1` на compose DB `pu_test` | **2021 passed, 4 failed, 59 skipped, 43 warnings**, 1373.22 s |
| Повтор четырёх failures на корректной `pu_workspace_test` | **4 passed**, 54.40 s |
| Эквивалентный итог backend на корректном тестовом DSN | **2025 passed, 0 failed, 59 skipped** |
| Frontend Vitest | **55 files, 301 tests passed** |
| TypeScript / production build | PASS; warning: основной JS chunk **714.56 kB** (>500 kB) |

Четыре исходных failure не являются падениями бизнес-логики: каждый отказался работать с именем `pu_test`. Но это реальный дефект `docker-compose.ci.yml`, потому что документированный compose-стенд не воспроизводит обязательный полный прогон без ручной смены DSN.

### Раскрытие skips

Все доступные категории opt-in были запущены на PostgreSQL 16 в изолированных БД/схемах; POSIX-only тест дополнительно выполнен в Linux-контейнере под UID 1000. Суммарно по целевым повторным запускам: **223 passed, 1 failed**. Это число включает offline-тесты файлов, повторно исполненные рядом с их opt-in cases, поэтому не складывается с 2025 как число уникальных тестов.

| Набор | Результат |
|---|---|
| LLM extraction + invoice + snapshots + organizer | 10 PASS |
| MVP3 PostgreSQL | 18 PASS |
| v5.4 foundation + authority | 91 PASS, **1 FAIL** |
| provider migration + autonomy + staging safety | 18 PASS |
| v5.4/MVP5 integration, row locks, context, mailbox, materialization, evidence | 61 PASS |
| mobile sync + Track E outbox + finance pin + OAuth race | 24 PASS |
| non-root POSIX staging path | 1 PASS |

Нормальные skips: live provider credentials, platform-specific execution и опциональные destructive/live acceptance gates, если они отдельно запускаются вручную. Ненормальный пробел: authority PostgreSQL concurrency был skipped в основном regression и стабильно красный при включении.

Дубликаты: 325 Python test-файлов; точных побайтовых дублей не найдено. Среди 1594 `test_*` функций одинаковых имён не найдено. Противоречивых route contracts по дубликатам method/path не найдено: после раскрытия lazy routers — 262 операции, 0 duplicate pairs.

## 5. Архитектура и миграции

- Alembic: **88** migration-файлов, ровно один head `b62f9d3a4c10`. Исторические branchpoints корректно сведены merge revisions; забытых heads нет.
- Gmail send ставится в durable outbox через `queue_confirmed_action` в `backend/app/api/gmail.py:649` и `backend/app/api/mail.py:743`.
- Google Tasks/Calendar ставятся в очередь в `backend/app/api/tasks.py:184-186` и `:233-238`.
- SDK mutations выполняются worker runtime, а не HTTP handler: `backend/app/provider_actions/product.py:612`, `:632`, `:653`.
- Старые synchronous wrappers `backend/app/google_tasks.py`, `google_calendar.py`, `integrations/actions.py::publish_actions` остаются в дереве, но активных app call-sites не найдено. Это технический долг/риск повторного включения второй реализации.
- LLM extraction использует единый combined path с намеренным regex fallback; второй активной независимой orchestration не найдено.
- Pagination неоднородна: mail — keyset cursor, documents — offset, virtual workspace — offset под именем cursor, mail grouping частично materializes выборку. Это не текущий correctness bug, но API/scale debt.
- Основной runtime уже durable и многопроцессный, поэтому README-ограничение «один backend / нужен внешний broker» устарело.

## 6. Секреты и конфигурация

- Tracked env-файлы: только `.env.example` и `sales/bot/.env.example`.
- Repository scanner `scripts/check_ci_security.py`: PASS, 0 findings.
- Дополнительный binary-safe scan истории: 6008 blobs, 0 high-confidence private key / OAuth secret / GitHub token / Google API key findings.
- Значения секретов в отчёт не выводились.
- Root `docker-compose.yml` содержит небезопасный fallback password `pu_change_me`. Это не утечка, но риск случайного непроизводственного запуска с предсказуемым паролем.
- `GMAIL_AUTO_SYNC_ENABLED` используется compose/runtime, но отсутствует в `.env.example`, несмотря на уже случавшийся shared-vs-runtime env incident. Это configuration drift.
- Production compose передаёт приватный env-file всем четырём app-компонентам; структура корректна.
- Базовые образы используют плавающие теги (`python:3.12-slim`, `node:22-bookworm-slim`, `postgres:16-alpine`, `nginx:1.28-alpine`), без digest pinning.

## 7. Зависимости и безопасность

- Frontend `pnpm audit`: **0** vulnerabilities при 208 dependency packages. Vitest fix не откатился: `4.1.11`.
- MPXJ fix не откатился: `16.5.0` (на момент проверки доступен `16.8.0`).
- Отстающие frontend direct dependencies: React 19.2.8→19.3.0, Vite 7.3.6→8.3.0, Vitest 4.1.11→5.0.1, TypeScript 5.9.3→7.0.2, Playwright 1.58.2→1.63.0, jsdom 26.1.0→30.1.1. Major upgrades не смешивать с функциональными PR.
- Отстающие backend direct dependencies включают Alembic 1.16.5→1.20.0, SQLAlchemy 2.0.43→2.0.54, psycopg 3.2.9→3.3.6, google-api-python-client 2.179.0→2.200.0, google-auth 2.40.3→2.58.0, pypdf 6.16.2→6.19.0, Starlette 1.3.1→1.7.0, Uvicorn 0.35.0→0.53.0.
- Обязательные GitHub dependency/security checks на exact main зелёные; локальный `pip-audit` завершился с exit 0. Обновления следует делать малыми отдельными PR с regression.

## 8. Ветки и PR hygiene

Всего `origin/codex/*`: **108**. Строгие ancestors main: **81**. Ещё две squash-integrated MVP5 ветки. Итого фактически интегрировано: **83**, и все 83 remote-ref оставлены на сервере. Реально не интегрировано: **25**.

Порог stale в этом отчёте: **14 дней без commit**. Из 25 несмерженных веток 23 stale; исключения: `deploy-ownership-tracked-issue` (12 дней) и `pr28-root-compose-diagnostic` (6 дней).

### 25 реально несмерженных веток

| Ветка | Дата | Автор | Состояние/рекомендация |
|---|---|---|---|
| commercial-p2-yandex360 | 2026-09-01 | Codex | stale; отдельная storage-фича, переоценить |
| automation-setup | 2026-09-03 | Codex | stale; PR #1 closed |
| ci-smoke-integration | 2026-09-03 | Codex | stale fault-fixture |
| parallel-validation-final | 2026-09-03 | Codex | stale validation branch |
| integrate-telegram-xlsx-af3f59d | 2026-09-04 | Codex | stale/возможный duplicate |
| integrate-telegram-xlsx-c3f8256 | 2026-09-04 | Codex | stale/возможный duplicate |
| mail-client-integration | 2026-09-04 | Codex | stale; сравнить с merged production-integrate |
| mail-client-release | 2026-09-04 | Codex | stale; вероятно superseded |
| telegram-email-analysis | 2026-09-04 | Codex | stale; проверить уникальный XLSX diff |
| v54-final-integration | 2026-09-04 | Codex | stale historical integration |
| v54-wave2-integration | 2026-09-04 | Codex | stale historical integration |
| v54-wave3-integration | 2026-09-04 | Codex | stale historical integration |
| v54-wave4-integration | 2026-09-04 | Codex | stale historical integration |
| app-immersive-ui | 2026-09-05 | Codex | stale; superseded release branch merged |
| mail-provider-throttle-fix | 2026-09-05 | Codex | stale; compare with later repair |
| mvp1234-wave2-integration | 2026-09-05 | Codex | stale historical integration |
| sales-immersive-landing | 2026-09-05 | Codex | stale sales subtree |
| sales-immersive-release | 2026-09-05 | Codex | stale sales subtree |
| ui-stale-cache-recovery | 2026-09-05 | Codex | stale; determine whether superseded |
| linear-mention-pu-39-staging | 2026-09-08 | bigbrotherdmitriy-prog | stale; PR #18 closed |
| pu-39-staging-runbook | 2026-09-08 | bigbrotherdmitriy-prog | stale; PR #19 open |
| v7-execution-wave6 | 2026-09-08 | Codex | stale report branch |
| mvp1-phase2-review | 2026-09-10 | Codex | stale docs/runtime-gate branch |
| deploy-ownership-tracked-issue | 2026-09-12 | bigbrotherdmitriy-prog | не stale; docs issue, принять решение |
| pr28-root-compose-diagnostic | 2026-09-18 | bigbrotherdmitriy-prog | не stale; диагностическая ветка |

### 83 интегрированные, но не удалённые remote-ref

Автор всех ниже — Codex, если отдельно не указано; дата — дата tip. Две ветки со знаком `squash` интегрированы не по ancestry.

| Дата | Ветки |
|---|---|
| 2026-09-03 | browser-acceptance-hotfix; pu-7-rename-preview; task-review-followup; task-review-integration |
| 2026-09-04 | ci-deduplicate-runs; ci-node24-actions; google-oauth-main-integration; google-workspace-acceptance; mail-client-production-integrate; mpp-production-integrated; mvp-safe-organizer-v1; ocr-adaptive-psm*; platform-automation-integration; pu-33-ci-staging-browser-e2e; pu-33-staging-first-deploy-current-fix; pu-34-primary-promotion; staging-deploy-workflow; staging-first-deploy-current-fix |
| 2026-09-05 | app-immersive-ui-release; mvp1-5-unified; pu-35-mvp1-5-unified; pu-36-production-readiness |
| 2026-09-08 | pu-38-ci-shard-determinism; pu-mail-production-repair |
| 2026-09-11 | mvp1-main-integration* |
| 2026-09-12 | gmail-sync-archived-filter* |
| 2026-09-14 | mvp2-audit* |
| 2026-09-18 | ci-linear-optional; fix-mail-draft-approval-role; fix-organizer-sessions-table; frontend-vitest-cve-2026-84373; mvp2-adjacent-deadline; mvp2-provider-status-audit; pu-36-mail-production-repair |
| 2026-09-19 | fix-invoice-payment-due-date; fix-provider-reconcile-oauth-rotation; hotfix-react-dist-sync; invoice-cost-classification; mvp2-track-e-outbox*; mvp2-unknown-recovery; mvp3-v3-01-hybrid-deadline; mvp3-v3-02-deadline-refresh; mvp3-v3-03-live-e2e*; mvp3-v3-04-meeting-conflicts*; ux-invoice-upload-runtime |
| 2026-09-20 | fix-contact-replay-race; fix-document-empty-state-upload; fix-folder-upload-skip-unsupported; mvp3-closure |
| 2026-09-21 | mvp4-act-budget-actual; mvp4-ai-analysis-retry; mvp4-canonical-finance-chain; mvp4-contract-budget-proposals; mvp4-finance-source-pins; mvp5-auto-product-composition (squash); mvp5-s10-live-bridge (squash); staging-host-migration |
| 2026-09-22 | android-offline-sync; fix-analysis-navigation; fix-contract-candidate-discovery; fix-contract-content-overrides-filename; fix-contract-parent-and-sole-proprietor; fix-legacy-doc-upload; mvp5-auto-incident-alert*; mvp5-inbound-auto-producer; mvp5-owner-context-confirmation; mvp5-project16-shadow-prep*; mvp5-project17-cohort*; product-auto-internal-notification*; separate-gpr-finance-import*; storage-folder-double-click* |
| 2026-09-23 | android-informative-screens; fix-mpxj-cve-2026; gpr-dds-fullscreen-tabs; gpr-dds-unified-workspace; mvp6-cross-evidence; mvp6-preflight-panel |
| 2026-09-24 | dds-cancel-operation; fix-dds-intake-priority; fix-gpr-dds-production-layout; fix-monthly-dds-import; fix-mpxj-16-import; fix-workspace-theme-consistency |

`*` — tip author `bigbrotherdmitriy-prog` у части merge/maintenance tips; точная авторская информация сохранена git history. Перед удалением remote-ref всё равно требуется owner review; аудит ничего не удалял.

## 9. Документация

В `docs/audits` — **86 markdown-файлов**. Они полезны как хронология, но не являются единым текущим status source. Ниже каждый файл отнесён к одной категории.

### Актуальные решения/ограничения

- `mvp2-pr33-recovery-hardening.md`
- `mvp3-management-history-adr.md`
- `mvp4-schedule-budget-links-future-adr.md` — **открытое owner decision**: many-to-many allocation, сумма/процент, overlaps, revision semantics.
- `organizer-sessions-startup-race.md`
- `mvp2-outbox-status-verification.md` — архитектурная граница reconciliation всё ещё полезна, отдельные статусы провайдеров требуют live evidence.

### Исторические completion/evidence — факты полезны, статус нельзя читать как текущий

- `background-job-hardening-result.md`, `commercial-p0-p1-integration-result.md`, `commercial-p1-ocr-result.md`, `gmail-project-validation.md`, `legal-release-readiness-result.md`
- `mail-move-permission-fix.md`, `mail-production-repair-release.md`, `mail-sent-folder-refresh.md`
- `mvp1-5-unified-completion-2026-09-04.md`, `mvp1-completion.md`, `mvp1-main-integration-snapshots-completion.md`
- `mvp2-completion.md`, `mvp2-llm-extraction-implementation.md`
- `mvp3-v3-03-live-e2e.md`, `mvp3-v3-04a-meeting-conflicts.md`
- `storage-binding-validation.md`, `storage-picker-browser-e2e.md`, `storage-picker-ui-validation.md`
- `v54-c01-content-gap.md`, `v54-c07-deadline-precision.md`, `v54-ci-permissions-hardening.md`, `v54-context-communication-contract.md`, `v54-email-compensation.md`, `v54-evidence-product-api-ui.md`, `v54-gmail-a05-wiring.md`, `v54-gmail-attachment-staging.md`, `v54-local-upload-staging.md`, `v54-mailbox-identity-implementation.md`, `v54-mailbox-rollout-controls.md`, `v54-oauth-hotfix-port.md`, `v54-ocr-benchmark-gate.md`, `v54-provider-acceptance-harness.md`, `v54-provider-action-runtime.md`, `v54-staging-safety-hardening.md`, `v54-tz-coverage-2026-09-04.md`, `v54-wave3-ci-gate.md`, `v54-wave3-fault-gaps.md`, `v54-wave3-final-review.md`, `v54-wave3-release-audit.md`, `v54-wave3-release-reconciliation.md`, `v54-wave3-sbom-legal.md`, `v54-wave3-security-review.md`.

### Prefight/design/history — завершённый этап, не текущий статус

- `mvp1-main-integration-alembic-preflight.md`, `mvp1-main-integration-app-composition-preflight.md`, `mvp1-main-integration-ocr-preflight.md`, `mvp1-main-integration-snapshots-preflight.md`, `mvp1-main-integration-storage-adapters-preflight.md`, `mvp1-management-history-architecture-comparison.md`
- `mvp2-ai-extraction-preflight.md`, `mvp2-main-audit.md`
- `mvp3-search-saved-views-preflight.md`, `mvp3-v3-04b-resource-conflicts-backlog.md`
- `v54-action-trust-contract.md`, `v54-authority.md`, `v54-autonomy-authorization.md`, `v54-context-pilot.md`, `v54-contract-integration.md`, `v54-pilot-foundation.md`, `v54-pilot-ux-spec.md`, `v54-source-evidence-contract.md`, `v54-source-evidence-pilot.md`, `v54-staging-assessment.md`, `v54-trust-pilot.md`.

### Явно устаревшие runtime/status утверждения

- `mvp2-track-e-outbox-integration.md` всё ещё говорит **LIVE PROVIDER NOT RUN**, хотя позже появились outbox/live-recovery работы; документ надо дополнить, не переписывая исходную историю.
- `v54-live-provider-gate.md`, `v54-product-acceptance.md`, `v54-pilot-integration.md`, `v54-final-integration.md`, `v54-final-runtime.md`, `v54-final-integration.md`, `v54-final-runtime.md` сохраняют прежние `NOT RUN/BLOCKED/CONDITIONAL`; они не отражают последующий MVP5 product pilot.
- `v54-mailbox-cutover.md` говорит production cutover blocked и PostgreSQL NOT RUN; это исторический статус.
- `v54-acceptance-corpus.md`, `v54-final-integration.md`, `v54-final-runtime.md`, `v54-release-packaging-final.md` содержат старые числа regression и не должны использоваться как текущий health report.

### CI/validation history, полностью superseded текущим CI

- `ci-smoke-fault-preparation.md`, `ci-smoke-fault-result.md`, `ci-smoke-github-run-result.md`, `ci-smoke-integration-result.md`
- `final-ci-runtime-review.md`, `final-validation-ci-e2e-integration.md`
- `parallel-validation-final.md`, `parallel-validation-integration.md`, `queue-recovery-validation.md`
- `v54-release-packaging-final.md`, `v54-wave3-release-audit.md`.

Рекомендация: добавить `docs/audits/INDEX.md` с колонками `historical/current/superseded`, exact SHA и ссылкой на документ-преемник. Исходные доказательства не удалять.

README также требует обновления: заголовок всё ещё описывает «MVP безопасного органайзера», test section не перечисляет component/browser и отдельные PostgreSQL gates, а runtime limitation про один backend и отсутствие broker/leases противоречит уже работающим durable jobs, workers и scheduler.

## 10. Рекомендуемый порядок работ

1. Отдельный небольшой PR: исправить authority epoch concurrency; добавить обязательный PostgreSQL job для этого теста.
2. Отдельный CI PR: `pu_workspace_test`/guard consistency, полный checkout в `Dockerfile.ci` или чётко документированный mount; проверить clean compose одной командой.
3. Довести MVP5 live acceptance до формального результата после 7 дней и ≥10 действий; не смешивать с code fixes.
4. Создать docs status index и обновить README/`.env.example` (`GMAIL_AUTO_SYNC_ENABLED`).
5. Owner review веток: сначала 25 unmerged, затем безопасно удалить 83 integrated refs; отдельно решить PR #19.
6. После стабилизации — dependency batches, pagination contract и удаление dead synchronous provider wrappers.

## 11. Границы аудита

- Код, ветки, PR, конфигурация и тесты читались с `origin/main`; production data и реальные provider accounts не изменялись.
- Ничего не мерджилось, не закрывалось, remote-ветки не удалялись.
- Секреты не выводились и не сохранялись.
- Создавались только временные локальные Docker/PostgreSQL audit-базы.

## 12. Независимая перепроверка (Claude, 2026-09-24)

Ключевые количественные и качественные утверждения этого отчёта перепроверены во второй, независимой сессии (другой агент, отдельный docker-контейнер, отдельная одноразовая Postgres-база) — не просто приняты на веру:

| Утверждение отчёта | Метод перепроверки | Результат |
|---|---|---|
| HEAD `5300d1cee86f...`, PR #81 | `git fetch` + `git log --oneline -1 origin/main` | ✅ точное совпадение |
| 108 remote `codex/*`-веток | `git branch -r \| grep -c codex/` | ✅ точное совпадение |
| 81 строгий ancestor main | `git merge-base --is-ancestor` по каждой ветке | ✅ точное совпадение |
| 88 файлов миграций | `git ls-tree -r` по `backend/migrations/versions`, минус `.gitkeep` | ✅ точное совпадение |
| Ровно один Alembic head `b62f9d3a4c10` | Сборка образа на `origin/main`, `alembic heads` | ✅ точное совпадение |
| Критический баг `authority_epoch_required` | Одноразовый Postgres 16, воспроизведение `test_v54_authority_postgres.py::test_postgres_role_change_linearizes_before_dispatch_check` | ✅ **воспроизведено дословно**: `ValueError: authority_epoch_required` в `app/models/v54_authority.py:66`, `_authority_epoch_guard` |
| Секретов не найдено | Отдельный паттерн-скан (`AIza`, `ghp_`, `gho_`, `AKIA`, PEM-заголовки приватных ключей) по всему дереву `origin/main` | ✅ 0 совпадений |

Вывод: все проверенные утверждения подтвердились без расхождений. Отчёту можно доверять как источнику текущего состояния проекта на дату аудита. Временные контейнеры/БД и git worktree, использованные для проверки, удалены после завершения.

## 13. Поправка к §1/§12: `authority_epoch_required` — дефект теста, не production-кода

Повторный аудит (тот же внешний агент, второй проход) уточнил находку из §1/§12: падение — **дефект замороженного времени в самом тесте**, а не подтверждённая уязвимость production-логики авторизации. Фикс существует в незамерженном коммите `fae8c3a` ("test(authority): fix frozen PG clock and capture safe thread failures", 2026-09-08), достижимом только из двух stale-веток (`codex/mvp1-phase2-review`, `codex/v7-execution-wave6` — обе уже в списке §8 как "реально не смержены").

**Механизм** (перепроверен независимо, в этой же сессии, двумя способами):

1. `seed_authority()` пишет `AuthorityState.updated_at = NOW`.
2. Немодифицированный тест использует `AuthorityResolver(clock=lambda: NOW)` — та же временная метка для мутации.
3. `_authority_epoch_guard` (`app/models/v54_authority.py:65`) требует, чтобы `state.attrs.updated_at.history.has_changes()` было `True`. PostgreSQL хранит timezone-aware datetime точно; присвоение ровно той же метки, что уже загружена, не регистрируется SQLAlchemy как изменение — guard кидает `ValueError("authority_epoch_required")` до того, как тестовый сценарий вообще доходит до проверки конкурентности.
4. SQLite (offline regression) маскирует это: naive/aware-конвертация при обычном round-trip даёт неявное отличие, которого нет на Postgres.

Фикс из `fae8c3a` меняет **только тестовый файл**: вводит `AUTHORITY_MUTATION_NOW = NOW + timedelta(microseconds=1)` для мутации (seed остаётся на `NOW`), плюс безопасное перехватывание ошибок потоков и диагностический sentinel-протокол для CI. Production-код (`_authority_epoch_guard`, `AuthorityResolver`) не изменён ни на строку.

**Независимая проверка в этой сессии (двумя методами):**

| Проверка | Метод | Результат |
|---|---|---|
| Offline ORM-guard механизм | Прогнан `test_aware_seed_timestamp_requires_distinct_mutation_clock` из `fae8c3a` (без БД, чистая проверка `_authority_epoch_guard` на двух версиях `updated_at`) | ✅ подтверждает: с одинаковой меткой — `ValueError`, с `NOW + 1μs` — проходит |
| Живой Postgres с фиксом | Тот же `test_postgres_role_change_linearizes_before_dispatch_check`, но версия файла из `fae8c3a`, на свежей одноразовой БД (тот же образ на актуальном `main`, тот же метод, что дал падение в §12) | ✅ **1 passed** (было `1 failed` на немодифицированной версии) |
| Все 5 diagnostic-тестов из `fae8c3a` | `tests/test_v7_authority_ci_diagnostics.py` | ✅ 5 passed |

**Итоговая переоценка:** находка §1/§12 остаётся корректной как *факт* (тест реально и воспроизводимо падает на актуальном `main` под opt-in gate) — воспроизведение в §12 не было ошибкой. Но её **классификация как "критичный production-дефект"** была преждевременной: это дефект тестовой fixture (совпадение временных меток), фикс для которого уже существует, но не попал в `main`. Приоритет из §1 ("Критично: исправить authority epoch update/guard") стоит понизить и переформулировать — исправлять нужно **тест**, а не guard/resolver; guard работает корректно. Рекомендация §10.1 актуальна в части "добавить обязательный PostgreSQL job для этого теста" — но только вместе с портированием фикса `fae8c3a`, иначе job будет стабильно красным без всякой связи с реальными инцидентами.
