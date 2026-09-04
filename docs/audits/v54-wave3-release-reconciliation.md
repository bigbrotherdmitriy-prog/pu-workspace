# PU Workspace v5.4 Wave 3 — сверка релизного gate

Дата: 2026-09-04

Текущий кандидат: `f0c0e26ef7cb971b2cef3965ec91b490471b127d`

Текущая единственная Alembic head: `a54f001c0a09`

Базовый runtime-доказательный commit: `f869319e226d0563d9c95eec408adcf716ed7e9f`

Базовый runtime run: `33865331595`

Базовый artifact SHA-256:
`c9f076ab9fddc652904e2a10d918e64d9d459a93aaa389fb8e825572ba8b4575`

## Решение

**MVP5 code/contract scope: PASS. Новый кандидат: runtime CONDITIONAL до
повторного CI. Live-provider и коммерческая выдача: NOT READY.**

Commit `f0c0e26` закрывает прежние gaps `C01`, `C07`, `S02`, `S07` и `S08`
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

Эти runs нельзя переносить на новый commit: после них добавлены расширенные
process-fault probes, content-to-evidence pipeline, PostgreSQL multi-mailbox
acceptance и миграция `a54f001c0a09`. Поэтому текущий runtime gate честно
остаётся CONDITIONAL до нового запуска на точном `f0c0e26` или его docs-only
потомке.

## Закрытые gaps MVP5

| ID | Статус в `f0c0e26` | Фактическое изменение | Что ещё проверяет новый CI |
|---|---|---|---|
| `C01` | **CODE/CONTRACT PASS** | Synthetic-only pipeline читает реальные байты corpus TXT/MD, извлекает project/contract/deadline, создаёт точные Evidence coordinates и проводит результат через Context, DeadlineClaim, Trust, Task и receipt | PostgreSQL A/B/C phase на итоговом commit; production ingress намеренно не включён |
| `C07` | **CODE/CONTRACT PASS** | `DeadlineClaim` сохраняет `due_date`, `due_time` и fixed-offset timezone без молчаливого усечения; добавлена последовательная миграция `a09` | Чистый upgrade до `a09`, timed-claim regressions и downgrade guard |
| `S02` | **CODE/CONTRACT PASS** | PostgreSQL acceptance доказывает независимые origins для одинаковых provider message/thread IDs в двух mailbox | Исполнение mailbox PostgreSQL test в новом runtime run |
| `S07` | **CODE/CONTRACT PASS** | Process harness убивает процесс после commit pending intent и до enqueue, затем проверяет recovery одного intent/Task/receipt | Реальный spawn/kill/recovery на Linux runner |
| `S08` | **CODE/CONTRACT PASS** | Process harness проверяет pre-commit rollback и kill после business commit до queue completion с receipt replay без второго эффекта | Реальный spawn/kill/lease reclaim на Linux runner |

Эти пункты больше не входят в remaining gaps. Пока CI не запущен, их статус не
следует повышать до runtime PASS.

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

| Gate | Статус для `f0c0e26` | Условие следующего перехода |
|---|---|---|
| 13 критериев MVP5 — code/contract | **PASS, 13/13** | Не ослаблять fail-closed и approval/authority contracts |
| `C01`, `C07`, `S02`, `S07`, `S08` | **CODE/CONTRACT PASS** | Новый PostgreSQL/runtime CI на точном кандидате |
| Alembic graph | **CODE PASS** | Одна head `a54f001c0a09`; подтвердить чистым runtime upgrade |
| Базовый `f869319` runtime | **PASS** | Историческое доказательство базы, не нового delta |
| Новый `f0c0e26` runtime | **CONDITIONAL** | Runtime, durable queue, общий CI и Docker smoke должны быть зелёными на новом SHA |
| `P04` finance | **OUT OF MVP5 / OWNER DECISION** | Отдельное решение и backlog, если функция нужна |
| `S10` live provider | **SYNTHETIC PASS / LIVE NOT RUN** | Изолированный live-provider acceptance |
| Production enable | **BLOCKED** | Новый runtime PASS, live-provider gate и отдельное решение владельца |
| Коммерческая выдача | **BLOCKED** | Закрыть owner/legal/release artifacts |

## Следующий безопасный порядок

1. Запустить `v54-pilot-runtime.yml`, `durable-queue.yml`, общий CI и Docker
   smoke на точном новом кандидате.
2. Принять runtime только если safe protocol показывает head `a54f001c0a09`,
   `result=PASS`, `cleanup=PASS`, выполненные `C01/C07/S02/S07/S08` и remaining
   gaps только `P04/S10`.
3. Провести отдельный live-provider sandbox acceptance для `S10`.
4. Параллельно закрыть owner/legal/release документы.
5. Не выполнять merge или production deploy без отдельного решения владельца.
