# PU Workspace — MVP1–MVP4 wave 2 integration

Продолжение после этого checkpoint: [parallel final hardening](mvp1234-final-hardening.md).
Последующий инкремент и карта полного ТЗ: [closure increment 2026-09-05](tz-closure-increment-2026-09-05.md).
Ниже сохранены исторические результаты этого этапа, а не метрики текущего HEAD.

Дата: 2026-09-05
Ветка: `codex/mvp1234-wave2-integration`

## Решение

Кандидат объединяет synthetic/offline реализации MVP1–MVP4; это не завершение
всего ТЗ и не подтверждение готовности live-сценариев. Ветка сохраняет один
`BackgroundJob`, exact Source/Evidence, CAS, append-only history и обязательное
подтверждение человеком для внешних, финансовых и юридически значимых действий.

Production не изменялся. Реальные Google, Яндекс, Gmail, Telegram, банковские
данные и документы клиентов не использовались.

## Что добавлено этой волной

### MVP1

- завершённый snapshot теперь можно явно обновить после добавления или изменения
  объектов через `refresh=true`;
- обновление создаёт новую immutable snapshot-версию и не переписывает прежнюю;
- в picker добавлена отдельная кнопка «Обновить изменения с диска»;
- сохранены project/provider/connection/folder guards и защита от возврата к
  старому Persistent Project;
- synthetic Google/Яндекс performance contract проверяет 2 304 объекта на
  провайдера без wall-clock порога.
- provider-neutral acceptance зафиксировал exact-pinned rename/move/rollback,
  компенсацию частичного сбоя, вложенные папки и запрет возврата к старому
  проекту;
- добавлены fail-closed `workspace.storage_mutation` job boundary с IDs-only
  payload и DB-only resolver exact current binding/source revision; live
  provider effect не активирован;
- synthetic-only runtime сохраняет durable attempt до эффекта, immutable receipt,
  безопасный replay без второго эффекта, UNKNOWN/reconciliation после crash и
  explicit rollback; реальные Google/Яндекс остаются default-deny.
- project-scoped API/UI дают redacted preview, manager-only confirm, measured
  progress и explicit rollback; исполнение возможно лишь при отдельном env flag
  и `synthetic:` connection, live подключения жёстко отклоняются.
- отдельный Chromium/runtime gate покрывает полный synthetic confirm → progress →
  receipt → rollback, stale project, viewer deny и UNKNOWN без автоповтора; CI
  готовит PostgreSQL, две worker identities, crash и lease recovery.
- capability-gated Google/Яндекс wrappers оставлены hard-deny: в текущих клиентах
  не реализован проверенный atomic expected-revision/ETag precondition для rename/move;
  HTTP-mock тесты подтверждают ноль live запросов даже при ошибочном enable.

### MVP2

- Gmail send, Google Tasks и Calendar переведены на существующий durable
  ProviderAction outbox;
- product policy разрешает только `CONFIRM` и явный allowlist действий Google;
- UNKNOWN не повторяет эффект вслепую и требует reconciliation;
- добавлены project/tenant-scoped list/detail/status endpoints;
- read model не раскрывает payload, адреса, токены, Evidence pins, provider
  response или raw error.
- в Management Center добавлен безопасный центр внешних действий: бизнес-статус,
  UNKNOWN/reconciliation и квитанции; retry/reconcile остаются защищены точной
  ревизией и policy.
- исправлены два сквозных дефекта: завершённая reconciliation job теперь не
  теряет `job_id`, а повторный HTTP reconcile корректно сообщает
  `already_queued=true` и не создаёт второе задание.
- Gmail sync читает bounded multi-page выдачу; повтор low-confidence письма не
  создаёт ResponseDraft до human context confirmation. Mailbox origin,
  SourceVersion/SourceCurrent и staging restart/lease проверены синтетически.
- Gmail read path получил bounded retry/backoff для 429/5xx/network, no-retry
  для 400/401/403 и повторную проверку credential generation/epoch перед каждой
  попыткой, включая attachment download; тексты provider errors не раскрываются.

### MVP3

- сохраняемые per-user/project настройки сводки с CAS;
- `daily`/`weekdays`, timezone, quiet hours и `in_app`/`disabled`;
- scheduler ставит IDs-only задания в существующий `BackgroundJob`;
- worker повторно читает текущую версию preference;
- immutable origin links для meeting/message proposals и повторная exact Evidence
  проверка до чтения/подтверждения;
- настройки доступны в Management Center;
- synthetic/offline M3-11 acceptance охватывает M3-01…M3-10.
- Chromium acceptance закрывает переключение проекта во время load/mutation,
  права viewer, фильтры attention и deny-by-default synthetic API.

### MVP4

- завершены immutable ГПР baseline, plan/fact, бюджет/ДДС, ручное подтверждение
  оплаты и отдельная корректировка;
- добавлена evidence-backed цепочка заявка → заказ → поставка → расхождение → акт;
- Supply Center подключён в раздел исполнения и финансов;
- добавлены шесть специализированных форм операций Supply Center с выбором
  exact-current Evidence, CAS, project/org guards и единым idempotency key в
  заголовке и теле запроса;
- explainable forecast показывает формулы, assumptions, confidence и Evidence;
- исправлены потеря Evidence бюджета в прогнозе, приём сумм с долями меньше
  копейки и мутация finance-записи до проверки stale status;
- synthetic/offline M4-10 acceptance подтверждает отсутствие AUTO платежей,
  подписей, писем и provider effects.
- из подтверждённого supply case создаётся только evidence-backed предложение
  ДДС со статусом `proposed`, `actual=0` и без payment/provider/job; подтверждение
  и корректировка остаются отдельными human-командами.
- mixed/unknown currency, VAT и retention теперь дают явный `decision_required`;
  приложение не смешивает валюты, не выбирает курс/ставку и не создаёт
  автоматическую оплату, проводку или конвертацию.

## Схема

Цепочка миграций остаётся линейной. Текущая единственная head:
`a54f001c0a18`.

- `a54f001c0a16` — supply cases, immutable versions и command receipts;
- `a54f001c0a17` — management digest preferences и immutable proposal origins.
- `a54f001c0a18` — mailbox/generation-scoped Gmail history checkpoints и события.

Readiness, Docker smoke и runtime pins обновлены на `a54f001c0a18`.

## Финальное закрытие локальной волны

- Gmail cursor: pin history до resync, bounded pagination, CAS, запрет advance
  при partial ingest/неполном listing, exact worker attempt/lease guard и stale
  release guard. Backlog свыше 100 писем не теряется молча: resync остаётся
  незавершённым. Расширенный resync пока не реализован.
- CI: отдельная owned database `puw_mvp2_test_gmail_history`, upgrade до текущей
  head и отдельная PostgreSQL CAS/schema фаза. Общий offline full-suite не
  выполняет эти PostgreSQL fixtures повторно. Cleanup использует существующий
  список CREATED; новые очереди и raw-log artifacts не добавлялись.
- Удалены два пустых PostgreSQL placeholder-теста с `assert True`. Вместо них
  подготовлена реальная проверка блокировки между двумя транзакциями, включая
  SQLSTATE `55P03` и успешный resolve после освобождения lock. Storage PostgreSQL
  fixtures теперь допускают только локальную тестовую БД и создают собственную
  UUID-схему с гарантированным удалением. Они используют ORM metadata;
  миграционный upgrade остаётся отдельной проверкой.
- Storage crash/replay fixture симулирует boundary сбоя и две worker identities
  в одном Python-процессе. Это НЕ доказательство kill/restart двух процессов;
  название CI step исправлено. Существующий отдельный v5.4 process-fault harness
  не заменяет ещё не выполненную проверку внешних storage effects.
- Managed copies: очистка только по точному сохранённому binding, без поиска
  имени; новые оригиналы других проектов исключены; история IDs сохранена;
  completed/replay имеют одинаковую безопасную форму результата. UI показывает
  успех только после completed с подтверждённым счётчиком и originals_affected=false.
  Новый managed copy path требует `supports_managed_copy_idempotency`, cleanup
  требует `supports_managed_copy_cleanup`. У реальных адаптеров эти capabilities
  не подтверждены: операции остаются заблокированы, а не выдаются за готовые.

## История завершающей интеграции

Поверх `dd0d332ccf9116ea8449dff7bc812b1f162045de`:

1. `0a1ae47a04c6bbc82cc5984727a311305be9fbcc` — замена пустых PG proofs.
2. `fa3a03bcf5dfd7d6b9eb3c3d7a2e9e1ff55d6bb8` — Gmail cursor, перенесён из
   `1a48d91b22e041deee879ad03af5d96ba9940f13`.
3. `b0189db23719c65c405077edeba034501ee183f0` — Gmail PostgreSQL CI phase.
4. `122df50f3d7fb1d3872b8fb09d9ceb0428f81bf1` — managed copies, перенесён из
   `d92c331222d4a618675ff37377f5d02637292023`.
5. `ad00d7aa42af9636b4a0c4671f67083ff7dffbb8` — runtime workflows включают
   текущую интеграционную ветку. Два новых regression сначала падали; после
   исправления branch triggers и сохранения read-only permissions — 22 targeted PASS.

Текстовых конфликтов не было. Автоматическое объединение `jobs/handlers.py`
проверено: сохранены `gmail.history.sync`, `workspace.safe_copy_cleanup` и
существующие handlers. Общий backend-прогон выполняется после обоих переносов.
Последний шаг меняет только CI, не product Core; полные backend/frontend наборы
относятся к Core SHA `122df50`. Финальный документационный commit следует за CI SHA; сам SHA отчётного
коммита доступен через `git rev-parse HEAD`.

## Проверки

| Проверка | Результат |
| --- | --- |
| Полный backend на итоговом code SHA `122df50` | `1481 passed, 25 skipped`, 502.50 s; 33 existing Alembic deprecation warnings |
| Gmail history/mailbox/staging targeted | `93 passed, 1 PostgreSQL skip` |
| Gmail provider fault acceptance | `40 passed` после интеграции; полный прогон форка `1392 passed, 21 skipped` |
| MVP1 mutation acceptance/job/resolver/runtime/API | `24 passed, 2 PostgreSQL skips` |
| MVP1 mutation Chromium | `4 passed`; интегрированный backend gate `10 passed, 2 PostgreSQL skips` |
| MVP1 live capability gate | `32 passed, 1 skip` на общей ветке; расширенный форк `47 passed, 3 PG skips` |
| MVP1 storage acceptance | `90 passed` |
| MVP2 provider controls/E2E | `41 passed`; полный прогон форка `1357 passed, 20 skipped` |
| MVP3 integrated acceptance/digest | `17 passed, 1 PostgreSQL skip`; Chromium `25 passed` |
| MVP4 integrated financial/supply regression | `36 passed` после DDS/verified forms |
| MVP4 finance decision guards | backend `38 passed`, frontend `27 passed` после интеграции |
| Полный frontend на итоговом code SHA `122df50` | `207 passed` |
| Frontend TypeScript check | PASS |
| Frontend production build | PASS; осталось существующее предупреждение о размере chunk |
| Chromium E2E на итоговом code SHA `122df50` | `30 passed`, 46.2 s; synthetic API fixtures |
| MVP3 CI/runtime contracts | `17 passed`; PostgreSQL runtime test skipped без test DSN |
| Alembic heads / CURRENT_SCHEMA_REVISION | одна: `a54f001c0a18`, совпадает |
| CI Python contracts включая durable harness | `135 passed`, 111.31 s; без shell-модуля |
| CI shell/mock workflow contracts | `22 passed`, 2.24 s; Git Bash + Python UTF-8 |
| CI после branch-trigger fix | `22 passed`, 0.78 s; полный прежний набор не подменяет эти повторные проверки |
| Acceptance corpus | structural PASS: 28 cases, 14 assets, 31 negative checks; application/runtime NOT_RUN |
| UX state logic | `18 passed` |
| Docker Compose / actionlint | локально недоступны; NOT_RUN |

Строки targeted отражают промежуточные интеграционные прогоны; строки final code
SHA относятся к единому кандидату. Старый полный backend `1366 passed, 20 skipped`
не считается результатом нового SHA. PostgreSQL/Docker runtime статическими
тестами не подменяется.

Общий backend завершён с exit code 0. 25 skipped не засчитаны как PASS;
PostgreSQL и платформенно-зависимые сценарии остаются отдельными gates.
Полный backend запускался после всех product-code изменений; последующий
commit содержит только CI triggers и их regression-тесты. Сборка и browser
fixtures не подключались к реальным API. Проверка имён изменённых файлов от
`dd0d332` не обнаружила .env, private-key файлов или каталогов production/log
exports; это узкий path scan, а не полный secret/content scan release bundle.

У shell/mock тестов первый запуск с Windows locale имел 3 ошибки декодирования
UTF-8 файлов с кириллическим путём; повтор при `PYTHONUTF8=1` и Git Bash прошёл
все 22. Исходные тесты/проверки не изменялись. Это не реальный Docker teardown.
Команда Alembic из repository root сначала не нашла относительный migrations;
корректная команда из backend прошла. Build-generated react_dist возвращён к
состоянию HEAD; два новых hashed assets удалены как воспроизводимые build outputs.

Команды финальных локальных проверок (Python — общий `.venv-pu-workspace-tests`):

```powershell
# cwd: backend
python -m pytest tests -q --tb=short --basetemp=.pytest-integrated-final-20260905
python -m alembic -c alembic.ini heads
# cwd: frontend
npm.cmd test -- --run
npm.cmd run check
npm.cmd run check:e2e
npm.cmd run build
npm.cmd run test:e2e
# cwd: repository root
python -m pytest scripts/ci -q --ignore=scripts/ci/tests/test_smoke_workflow.py --basetemp=.pytest-ci-integrated-final
$env:PATH = 'C:/Program Files/Git/bin;' + $env:PATH
$env:PYTHONUTF8 = '1'
python -X utf8 -m pytest scripts/ci/tests/test_smoke_workflow.py -q --tb=no -p no:cacheprovider --basetemp=.pytest-ci-bash-utf8-final
python docs/acceptance/v54-corpus/validate.py --self-test
node --test docs/ux/v54-pilot/state.test.cjs
git diff --check
```

## Открытые gates

- MVP1: live OAuth/provider revision/rename-move-rollback и latency на реальных
  тестовых Google/Яндекс аккаунтах; browser/mock сценарии закрыты локально;
  managed cleanup требует lease-fencing непосредственно перед эффектом и
  provider ownership/reconciliation proof; copy crash reconciliation не закрыта;
- MVP2: live Gmail history/pagination, attachment restart/lease и sandbox
  timeout-after-effect/reconciliation; расширенный full resync >100 сообщений;
  PG cursor fixture доказывает CAS, но не полноценное worker lease recovery;
- MVP3: PostgreSQL concurrency и один выбранный live channel; browser acceptance
  закрыт локально (общий Chromium-набор `30 passed`);
- MVP4: PostgreSQL concurrency/upgrade/backup-restore, решение владельца по
  multi-currency/VAT/retention и заключение юриста/бухгалтера по назначению
  ручного подтверждения оплаты;
- весь кандидат: изолированный GitHub Actions runtime после отдельного разрешения
  на push.

До закрытия live/owner/legal/runtime gates результат является
`LOCAL SYNTHETIC PASS / RELEASE CONDITIONAL`.

## Следующий внешний прогон (не выполнен)

После отдельного разрешения на публикацию итогового SHA:

```powershell
git push -u origin codex/mvp1234-wave2-integration
# Push запускает оба workflow; команды ниже — только для отдельного повторного
# прогона, если он нужен. Не запускать дубликаты поверх уже работающего run.
gh workflow run v54-pilot-runtime.yml --ref codex/mvp1234-wave2-integration
gh workflow run storage-mutation-runtime.yml --ref codex/mvp1234-wave2-integration
```

Проверить headSHA каждого run и безопасный protocol, а не только зелёный статус.
Storage workflow проверяет локальную симуляцию провайдера; live mutation и cleanup
этим не включаются. Merge, PR, push и deploy в ходе этой локальной интеграции не
выполнялись. Основная dirty worktree не редактировалась; её docker-compose.yml,
docs/legal и прочие пользовательские untracked файлы сохранены. Production не
использовался.
