# PU Workspace v5.4 — S10 live-provider gate

Дата: 2026-09-04

Ветка: `codex/v54-live-provider-gate`

База: `8ccc194bc834328e51a73225981f74d81775789a`

## Решение

**GATE/CONTRACT PASS; LIVE PROVIDER NOT RUN.**

Подготовлен отдельный, выключенный по умолчанию контур для единственного
оставшегося сценария `S10`. Никакие credentials не использовались, сетевой
provider effect не выполнялся и статус S10 не повышался до PASS.

## Аудит до изменения

- Product runtime уже сохраняет `UNKNOWN` после timeout-after-effect и переводит
  повторного worker на lookup/reconciliation вместо второго dispatch.
- Existing fake-provider harness проверяет `dispatch=1`, reconciliation и
  `effects=1`, но является полностью синтетическим.
- Основной PostgreSQL workflow намеренно сообщает `S10` как expected gap.
- Отдельного защищённого live sandbox workflow, attestation, content-free bridge
  contract и безопасного результата `NOT_RUN` не было.

## Реализовано

- `scripts/ci/v54_live_provider_gate.py`:
  - явный acknowledgement;
  - `NOT_RUN` при выключенном gate или отсутствующих test secrets;
  - production-like/небезопасный endpoint refusal;
  - запрет sender/recipient/address inputs;
  - exact sandbox attestation;
  - один dispatch с timeout-after-effect;
  - lookup без blind retry;
  - доказательство exactly-one observed sink effect;
  - cleanup в `finally`;
  - allowlisted error codes и атомарный safe protocol.
- `.github/workflows/v54-live-provider-acceptance.yml`:
  - только ручной `workflow_dispatch`;
  - boolean input по умолчанию `false`;
  - protected Environment `v54-live-provider-sandbox`;
  - `contents: read`, ограниченный timeout;
  - artifact содержит только `protocol.json`;
  - отсутствие secrets не маскируется как PASS.
- `scripts/ci/test_v54_live_provider_gate.py`:
  regression/contract coverage default-off, unsafe endpoint, адресов,
  attestation, timeout, reconciliation, duplication, no blind retry, cleanup и
  отсутствия чувствительных данных.
- Runbook определяет bridge API, secrets, запуск, критерии приёмки и аварийную
  очистку.

## Threat boundaries

| Риск | Контроль |
|---|---|
| Случайный автоматический внешний эффект | Workflow только ручной; input default false; отдельный acknowledgement |
| Использование production endpoint | HTTPS + sandbox/test host marker + deny `prod`/IP/localhost + exact hostname hash + runtime attestation |
| Доставка реальному человеку | Workflow не принимает адреса; bridge обязан иметь sink-only/no-external-delivery policy |
| Двойной эффект после timeout | Ровно один dispatch; затем только lookup; `observed_effects` обязан быть 1 |
| Утечка данных в artifact | Закрытая схема protocol, hashes/counters/enums; без URL, payload, email, token, DSN и raw stderr |
| Runner погиб до cleanup | Bridge обязан иметь короткий TTL и scoped cleanup по run nonce |

## Статус S10

| Слой | Статус |
|---|---|
| Synthetic provider contract | PASS (существующее доказательство) |
| Product UNKNOWN/reconciliation orchestration | PASS (существующие тесты/runtime) |
| Default-off live gate contract | PASS после локальных contract-тестов |
| Protected sandbox bridge | NOT PROVIDED |
| Фактический network timeout-after-effect | NOT RUN |
| Exactly-one live observed effect | NOT RUN |
| Фактический live cleanup | NOT RUN |

Следовательно S10 остаётся **LIVE NOT RUN**. Этот коммит уменьшает оставшуюся
работу до развёртывания bridge, настройки четырёх test-only secrets и одного
ручного запуска на точном release SHA.

## Проверки

```text
python -m pytest scripts/ci/test_v54_live_provider_gate.py -q
29 passed

python -m pytest scripts/ci -q
153 passed

python -m pytest \
  tests/test_v54_provider_acceptance_contract.py \
  tests/test_v54_provider_action_runtime.py \
  tests/test_v54_provider_action_migration.py -q
45 passed, 1 conditional PostgreSQL skip
```

Полный `scripts/ci` suite повторён с ASCII `--basetemp`: известная локальная
проблема Windows/MSYS с кириллицей в OneDrive-пути не переносится на Linux
runner. `py_compile`, YAML contract parse и `git diff --check` — PASS.
`actionlint` локально недоступен; фактический GitHub workflow не запускался.
Network/provider проверки не подменялись статическими тестами.

## Не изменено

- product core и provider action runtime;
- Gmail/Google/Yandex execution paths;
- BackgroundJob, миграции и Alembic head;
- production, OAuth, DNS и данные пользователей.

Push, merge, PR и deploy не выполнялись.
