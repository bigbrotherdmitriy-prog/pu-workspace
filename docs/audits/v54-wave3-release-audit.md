# PU Workspace v5.4 Wave 3 — release/security audit

Дата: 2026-09-04

Ветка аудита: `codex/v54-wave3-release-audit`

Проверенный commit: `842215fb1b977a0e50e08a025cf6f8a69d8a6b69`

База сравнения: `f721634762944e8bf9020e99c50f504678291296`
Диапазон: `f721634..842215f` (118 файлов, 13 159 добавлений, 386 удалений)

## Решение

- **P0: 0.** Активируемой утечки секрета, документа или внешнего действия без
  authority/approval не найдено.
- **Wave 3 security delta: PASS по доступным статическим и локальным contract-
  проверкам.** Новые production integration points остаются fail closed или
  требуют явной DB-backed policy/authority.
- **Технический release candidate: CONDITIONAL.** Три PostgreSQL-only migration-
  проверки не выполнены локально без disposable DSN; нужен зелёный isolated CI
  на точном candidate SHA.
- **Внешняя коммерческая поставка: FAIL до снятия P1 release blockers.** Два из
  них унаследованы от базы (SBOM/LICENSE), один относится к общему workflow,
  который запускается и для Wave 3.

## P1 — блокеры

### P1-01. Общий CI не задаёт least privilege

`.github/workflows/ci.yml:3-6` запускается на `codex/**`, следовательно и на
`codex/v54-wave3-integration`. При этом workflow не содержит верхнеуровневого
`permissions`, а `actions/checkout@v4` на строке 29 не задаёт
`persist-credentials: false`. Затем код репозитория и пакетов исполняется на
строках 35-71.

Риск: фактический scope `GITHUB_TOKEN` зависит от настройки репозитория, а
checkout credential может быть доступен исполняемому test/build-коду. Реальных
production secrets workflow сейчас не передаёт, однако граница least privilege
не доказана.

Требуется до merge/release:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v4
    with:
      persist-credentials: false
```

После изменения нужен contract-test, проверяющий оба условия.

### P1-02. Authoritative SBOM устарел и frontend parser формирует ложные пакеты

`NOTICE:7-9` объявляет три файла `docs/release/generated/sbom-*.spdx.json`
authoritative. Фактически:

| Слой | Tracked packages | Регенерация на `842215f` | Дефект |
|---|---:|---:|---|
| backend | 13 | 14 | отсутствует runtime `Pillow==11.3.0` из `backend/requirements.txt:14` |
| frontend | 219 | 223 | отсутствуют актуальные Playwright-компоненты; 7 ложных записей |
| containers | 9 | 9 | расхождения количества не найдено |

Причина ложных frontend-компонентов: `parse_pnpm_lock` в
`scripts/legal_release_kit.py:104-120` входит в секцию `packages:`, но не
останавливается на следующей top-level секции. Regex на строке 113 принимает
вложенные snapshot mappings за packages. В tracked и заново сгенерированном
frontend SPDX присутствуют записи с пустым/отступленным именем и некорректным
purl, например около `docs/release/generated/sbom-frontend.spdx.json:20-28`.

Это унаследованный release blocker, не регрессия Wave 3: manifest-файлы
`backend/requirements.txt`, `frontend/package.json` и `frontend/pnpm-lock.yaml`
не менялись в проверенном диапазоне. Новые модули используют уже заявленные
runtime-зависимости.

Требуется до внешней поставки:

1. исправить разбор top-level секций pnpm lock;
2. добавить regression fixtures для scoped packages и `snapshots:`;
3. заново сформировать все три SPDX на итоговом release SHA;
4. сверить direct и transitive graph, версии, purl и container digests;
5. повторно подписать release manifest/checksum после регенерации.

### P1-03. LICENSE/NOTICE и лицензии компонентов не закрыты для продажи

`LICENSE:3,14-15` содержит незаполненного правообладателя и прямо называет файл
техническим placeholder. `NOTICE:16-19` требует закрыть все `NOASSERTION` и
приложить обязательные license/notice texts. Текущее количество packages с
`NOASSERTION`: backend 13/13, frontend 219/219, containers 9/9.

Это унаследованный юридический blocker, а не Wave 3 regression. До коммерческой
выдачи нужны решение правообладателя, проверка профильного юриста, разрешённые
SPDX license expressions и полный LICENSE/NOTICE bundle для фактического графа.

## P2 — hardening

### P2-01. Общий CI публикует raw test/build logs

`.github/workflows/ci.yml:43-45,59-71` пишет полный stdout/stderr в четыре log-
файла, а строки 72-83 публикуют их на 30 дней. Сейчас workflow использует только
синтетический PostgreSQL DSN и не получает repository secrets, поэтому прямой
утечки не найдено. Но traceback/assertion будущего теста может содержать payload
или PII.

Рекомендация: не публиковать raw logs; сохранять allowlisted counters, node IDs и
safe error categories, как в Wave 3 runtime protocols. Срок хранения уменьшить
до 7 дней.

### P2-02. CI supply-chain references не immutable

Workflow использует mutable major tags для actions, mutable container tags
`python:3.12-bookworm` и `postgres:16-alpine`, а durable workflow дополнительно
устанавливает неприкреплённый `PyYAML`. Это не внесло новую runtime-зависимость
продукта, но снижает воспроизводимость и увеличивает CI supply-chain риск.

Рекомендация: actions закрепить по commit digest с комментарием версии,
контейнеры — по digest, CI Python packages — точными версиями/lock-файлом.

## Проверенные инварианты Wave 3

### Секреты, PII и содержимое

- По всем 790 tracked paths не найдено сигнатур AWS/GitHub/OpenAI/Slack keys или
  PEM private keys.
- В добавленных строках найдены только синтетические адреса доменов
  `example.test`/`example.invalid`.
- Упоминания `private-access-token`, `private-refresh-token`, `SECRET ...` и
  `C:/private/...` находятся только в negative synthetic tests и проверяют
  отсутствие этих значений в payload/audit/log.
- `.env` в git не отслеживается; отслеживаются только `.env.example` шаблоны.
- Wave 3 не изменяет production credentials, OAuth settings или deployment
  secrets.

### Единственная очередь и безопасные payload

- Единственная универсальная очередь — `background_jobs` в
  `backend/app/models/job.py`.
- Local upload payload строго равен `{"staging_id": <opaque-32-hex>}`.
- Gmail attachment payload строго равен `{"staging_id": <opaque-32-hex>}`.
- Provider runtime payload содержит только `organization_id`, `action_id` и
  `revision`.
- Synthetic pilot payload содержит только tenant/action/revision и UUID
  correlation; UUID проверяется до enqueue/execute.
- File bytes, base64, document text, email body, provider locator, OAuth token,
  KEK/DEK, DSN и filesystem path в этих payload непредставимы.
- `v54_provider_dispatch_outbox` является транзакционным outbox, привязанным к
  `background_jobs.job_id`, а не второй исполняющей очередью.
- `ProviderOutcomeObservation` хранит append-only provider facts; business
  Action Ledger остаётся `v54_actions`/`v54_action_revisions`/`v54_receipts`.
  Второго business ledger или Source registry не обнаружено.

### Default-off и authority

- Mailbox rollout flags имеют server default `false` и включаются owner-only CAS
  переходами по монотонной lattice.
- Local upload runtime не устанавливается в application startup; без явной
  composition API и worker возвращают безопасную unavailable-ошибку.
- Gmail attachment lifecycle не устанавливается startup-кодом; provider body до
  explicit lifecycle/authority не читается.
- Evidence fragment store отсутствует в startup и без server-installed store
  возвращает одинаковое non-cacheable unavailable.
- Provider adapter допускает только `synthetic`, `CONFIRM` и test DB; startup его
  не устанавливает.
- Autonomy policy отсутствует по умолчанию. AUTO возможен только после явного
  owner policy assignment, только для `task.internal.create`, LOW,
  COMPENSATABLE и точного effect set; external/unknown остаются CONFIRM/DENY.
- Миграции не seed'ят policy, action, approval или rollout enablement.

### Alembic

- `alembic heads`: ровно `a54f001c0a08`.
- Цепочка Wave 3 линейна: `a04 -> a05 -> a06 -> a07 -> a08`.
- `backend/app/schema.py`, Docker readiness, durable harness и v5.4 runtime
  ожидают `a54f001c0a08`.
- `a08` не молча принимает старые duplicate local-upload documents: migration
  останавливается с требованием ручной проверки.
- Реальный upgrade PostgreSQL в этой локальной audit-worktree не выполнялся.

### CI artifacts и cleanup

- Изменённые `v54-pilot-runtime.yml`, `durable-queue.yml` и `docker-smoke.yml`
  используют `permissions: contents: read` и checkout без persisted credentials.
- Публикуются только точные allowlisted JSON/protocol paths; glob raw output в
  durable workflow удалён.
- Raw subprocess stdout/stderr/arguments не входят в Wave 3 protocols; сохраняются
  только размеры, allowlisted category, exit/status/counters.
- Durable cleanup имеет `if: always()`, exact project-name validation и выполняется
  до публикации protocol. Docker smoke проверяет отсутствие контейнеров, networks,
  volumes и удаление временного env/log.
- v5.4 service container и job container уничтожаются GitHub-hosted runner после
  job; созданные внутри PostgreSQL test databases также удаляются в `finally`.

### Build assets и зависимости

- Новый runtime dependency manifest в диапазоне не изменялся.
- Production frontend build в `backend/app/react_dist` согласован: `index.html`
  ссылается на единственные tracked hashed JS/CSS assets; независимый frozen
  build дал те же 10 файлов после нормализации Git EOL.
- Новых изображений, шрифтов, иконок, медиа или иных assets с отдельным provenance
  в Wave 3 не добавлено.

## Выполненные команды и результаты

```text
git merge-base 842215f f721634
  f721634762944e8bf9020e99c50f504678291296

python -m alembic -c alembic.ini heads
  a54f001c0a08 (head)

python -m pytest <Wave3 CI/schema/staging/provider/autonomy/upload/Gmail/evidence targets>
  131 passed, 3 skipped

python -m pytest <a08/provider/a07 migration targets> -rs
  14 passed, 3 skipped
  skips: disposable PostgreSQL URLs are not configured

python scripts/legal_release_kit.py sbom --ref 842215f --out <isolated-temp>
  backend=14, frontend=223, containers=9, malformed frontend entries=7

git grep <high-confidence secret signatures>
  no matches

git diff --check f721634..842215f
  PASS
```

Дополнительно выполнены read-only manifest comparison, job payload inventory,
ORM table inventory, workflow/artifact review, tracked asset rebuild comparison и
проверка шаблонных env-файлов. Временные файлы удалены; worktree оставлена чистой
до добавления этого отчёта.

## Точные условия снятия блокеров

1. Исправить `ci.yml` least privilege и raw-log artifact policy; запустить все
   workflows на итоговом SHA.
2. Исправить pnpm SBOM parser, регенерировать SBOM/NOTICE bundle и проверить graph.
3. Снять все `NOASSERTION`, заполнить правообладателя и получить юридическое
   согласование LICENSE/NOTICE.
4. Получить зелёные PostgreSQL migration/concurrency/runtime и durable fault jobs
   именно для итогового Wave 3 SHA.
5. До выполнения пунктов 1-4 не выполнять production deploy и не маркировать
   кандидат как коммерчески готовый.
