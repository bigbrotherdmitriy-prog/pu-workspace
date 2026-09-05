# PU Workspace v5.4 Wave 3 — сверка релизного gate

Дата: 2026-09-04

Проверенный кандидат: `8ccc194bc834328e51a73225981f74d81775789a`

Текущая единственная Alembic head: `a54f001c0a09`

Базовый runtime-доказательный commit: `f869319e226d0563d9c95eec408adcf716ed7e9f`

Базовый runtime run: `33865331595`

Базовый artifact SHA-256:
`c9f076ab9fddc652904e2a10d918e64d9d459a93aaa389fb8e825572ba8b4575`

## Решение

**MVP5 code/contract scope: PASS. Изолированный PostgreSQL/Linux runtime:
PASS. Live-provider и коммерческая выдача: NOT READY.**

Финальный кандидат `8ccc194` прошёл все четыре обязательных GitHub Actions на
точном SHA:

- `v5.4 synthetic pilot PostgreSQL runtime`, run `33872553514`, `6m28s` — PASS;
- `Durable queue recovery`, run `33872553425`, `4m02s` — PASS;
- `PU Workspace CI`, run `33872553588`, `3m56s` — PASS;
- `Docker smoke`, run `33872553529`, `54s` — PASS.

Безопасный runtime artifact имеет SHA-256
`4f0f29da5576891b7ab4aa48d4f6530d04d3ae2373ce82dbf7488179b886da78`.
Его закрытая схема `puw.v54.runtime.protocol.v1` подтвердила
`result=PASS`, `cleanup=PASS`, head `a54f001c0a09`, полный backend
`1132 passed / 16 skipped`, PostgreSQL A/B/C integration
`301 passed / 0 skipped` и process-fault проверки `S07/S08` без повторных
Task, receipt, projection или success audit. Raw output не публиковался.

Commit `8ccc194` закрывает прежние gaps `C01`, `C07`, `S02`, `S07` и `S08`
кодом и исполняемыми contract-тестами. Runtime protocol теперь считает
исполненными `C01`, `C07`, `P02`, `P06`, `S02`, `S06`, `S07`, `S08`, `S09` и
оставляет только:

- `P04` — финансовый контур вне MVP5;
- `S10` — live external UNKNOWN/reconciliation не выполнялся.

Run `33865331595` остаётся валидным доказательством базового commit `f869319`:
`1123 passed / 15 skipped` backend, `293 passed / 0 failed` PostgreSQL,
`process_reclaim=PASS`, `cleanup=PASS`, head `a54f001c0a08`. Durable queue run
`33865331624`, общий CI `33865331644` и Docker smoke `33865331681` также были
PASS на этой базе.

Исторические runs нельзя было переносить на новый delta. Поэтому выполнен новый
набор из четырёх workflow на точном `8ccc194`; он подтвердил delta, head `a09`
и итоговый runtime PASS.

## Закрытые gaps MVP5

| ID | Статус в `8ccc194` | Фактическое изменение | Итоговое runtime-доказательство |
|---|---|---|---|
| `C01` | **RUNTIME PASS** | Synthetic-only pipeline читает реальные байты corpus TXT/MD, извлекает project/contract/deadline, создаёт точные Evidence coordinates и проводит результат через Context, DeadlineClaim, Trust, Task и receipt | PostgreSQL A/B/C phase прошла; production ingress намеренно не включён |
| `C07` | **RUNTIME PASS** | `DeadlineClaim` сохраняет `due_date`, `due_time` и fixed-offset timezone без молчаливого усечения; добавлена последовательная миграция `a09` | Чистый upgrade до `a09`, timed-claim regressions и downgrade guard прошли |
| `S02` | **RUNTIME PASS** | PostgreSQL acceptance доказывает независимые origins для одинаковых provider message/thread IDs в двух mailbox | Mailbox PostgreSQL test исполнен в итоговом runtime run |
| `S07` | **RUNTIME PASS** | Process harness убивает процесс после commit pending intent и до enqueue, затем проверяет recovery одного intent/Task/receipt | Реальный spawn/kill/recovery прошёл на Linux runner |
| `S08` | **RUNTIME PASS** | Process harness проверяет pre-commit rollback и kill после business commit до queue completion с receipt replay без второго эффекта | Реальный spawn/kill/lease reclaim прошёл на Linux runner |

Эти пункты больше не входят в remaining gaps и подтверждены итоговым
PostgreSQL/Linux runtime.

## Единственные оставшиеся функциональные ограничения

### `P04` — решение владельца и будущий финансовый scope

§31.8/31.9 не требует финансового execution для MVP5. Письмо со счётом не
подтверждает оплату; подтверждение оплаты является отдельным пользовательским
событием. Для интеграции с ДДС владелец должен утвердить DTO, роли, evidence,
связь с этапом, правила исправления и отдельные financial acceptance tests.
`P04` не блокирует узкий MVP5, пока финансовый execution явно остаётся вне его
scope.

### `S10` — live-provider gate

Synthetic provider уже проверяет UNKNOWN, lookup/reconciliation и запрет blind
retry. Не выполнен сетевой сценарий на изолированной тестовой учётной записи:
timeout-after-effect, повторное наблюдение provider state и доказательство
ровно одного внешнего эффекта. `S10` не является пробелом внутренних контрактов,
но блокирует заявление о live-provider readiness.

## Требования 1.0+ и будущих версий

Следующие пункты исходный §31.8 относит к `1.0+` или более позднему scope; они не
являются remaining MVP5:

- расширенная Company Memory;
- дополнительные специализированные AI Agents;
- richer enterprise Context Graph;
- enterprise policy automation;
- дополнительные provider adapters сверх пилотного пути;
- полноценный graph engine;
- автономное выполнение high-risk, финансовых, юридических, платёжных и
  destructive actions.

## Решения владельца вне code gate

- утвердить, что `P04` остаётся вне MVP5 либо открыть отдельный финансовый
  backlog;
- выбрать одно каноническое обозначение версии: имя DOCX содержит `v5_4`, титул
  документа — `5.1`;
- определить pilot/live-provider cohort и тестовые учётные записи;
- заполнить правообладателя и год в корневом `LICENSE`;
- утвердить модель лицензирования/сделки и точный состав передачи;
- подтвердить происхождение собственных PWA icons и цепочку прав;
- отдельно разрешить merge, production enable и deploy.

## Коммерческий и юридический gate

Runtime и MVP5 code PASS не закрывают коммерческую выдачу. Остаются:

### Release manager

- воспроизводимый Python transitive lock с hashes;
- digest-pinned container inventory и layer/apt SBOM;
- package-specific LICENSE/COPYING/NOTICE bundle;
- release archive/manifest/checksum после фиксации финального release SHA.

### Профильный юрист

- цепочка исключительных прав и разрешённые способы использования;
- `licenseConcluded`, обязанности по `psycopg`/LGPL и контейнерным слоям;
- финальный `LICENSE`/`NOTICE` и договорная модель;
- режим ПДн, AI providers/subprocessors, retention и data residency для
  реальных данных;
- пакет Реестра российского ПО, если владелец решает подавать заявление.

## Итоговый gate

| Gate | Статус для `8ccc194` | Условие следующего перехода |
|---|---|---|
| 13 критериев MVP5 — code/contract | **PASS, 13/13** | Не ослаблять fail-closed и approval/authority contracts |
| `C01`, `C07`, `S02`, `S07`, `S08` | **RUNTIME PASS** | Не ослаблять exact evidence, mailbox isolation и process-fault contracts |
| Alembic graph | **RUNTIME PASS** | Одна head `a54f001c0a09`, чистый runtime upgrade подтверждён |
| Базовый `f869319` runtime | **PASS** | Историческое доказательство базы, не нового delta |
| Новый `8ccc194` runtime | **PASS** | Все четыре workflow зелёные на точном SHA |
| `P04` finance | **OUT OF MVP5 / OWNER DECISION** | Отдельное решение и backlog, если функция нужна |
| `S10` live provider | **SYNTHETIC PASS / LIVE NOT RUN** | Изолированный live-provider acceptance |
| Production enable | **BLOCKED** | Live-provider gate и отдельное решение владельца |
| Коммерческая выдача | **BLOCKED** | Закрыть owner/legal/release artifacts |

## Следующий безопасный порядок

1. Провести отдельный live-provider sandbox acceptance для `S10` на тестовой
   учётной записи без production-данных.
2. Параллельно закрыть owner/legal/release документы.
3. Не выполнять merge или production deploy без отдельного решения владельца.
