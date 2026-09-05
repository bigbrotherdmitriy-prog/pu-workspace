# V5.4: изолированная live-provider приёмка S10

Дата: 2026-09-04

## Статус

Контур готов к подключению выделенного sandbox bridge, но без явно переданных
тестовых секретов результат всегда **`NOT_RUN`**, а не `PASS`. Эта проверка не
включает production, не отправляет сообщения реальным адресатам и не меняет
product runtime.

## Что доказывает успешный запуск

Один ручной запуск создаёт ровно один sink-only effect в выделенной эфемерной
тестовой области. Bridge применяет эффект, намеренно не возвращает ответ на
dispatch, после чего gate:

1. не повторяет dispatch;
2. выполняет только scoped lookup/reconciliation;
3. наблюдает `APPLIED` и `observed_effects=1`;
4. удаляет созданный тестовый объект;
5. публикует только content-free JSON protocol.

Это закрывает сетевую часть `S10` только при фактическом `PASS` artifact. Запуск
без bridge/credentials, contract-тесты и синтетический provider не являются
live-provider доказательством.

## Обязательная изоляция bridge

Bridge должен быть отдельным тестовым сервисом перед выбранным provider adapter.
Он не может принимать адрес получателя или отправителя из workflow. Sink
закрепляется внутри тестовой учётной записи и технически не допускает внешнюю
доставку.

До добавления secrets владелец bridge обязан подтвердить:

- hostname содержит отдельную метку `sandbox`, `test`, `testing`, `staging` или
  `qa` и не содержит `prod`;
- endpoint использует HTTPS, не IP и не localhost;
- account class — `ephemeral-test`;
- effect class — `sink-only`;
- address policy — `no-external-delivery`;
- поддерживаются `timeout-after-effect`, scoped lookup и cleanup;
- один `command_key` идемпотентен внутри exact account fingerprint;
- lookup возвращает число фактически наблюдаемых provider effects;
- bridge никогда не возвращает тело, адрес, токен или provider object ID.

## Content-free bridge API

Все запросы используют `Authorization: Bearer ...` и `X-PUW-Run-Nonce`. Полный
URL и token считаются секретами и не выводятся.

### `GET /v1/acceptance/capabilities`

Bridge возвращает **ровно**:

```json
{
  "schema": "puw.v54.live-provider.capabilities.v1",
  "environment": "ephemeral-test",
  "effect_class": "sink-only",
  "address_policy": "no-external-delivery",
  "cleanup": "supported",
  "fault": "timeout-after-effect",
  "account_fingerprint": "<sha256>",
  "run_nonce": "<sha256>"
}
```

### `POST /v1/acceptance/effects`

Gate передаёт только SHA-256 `action_id`, `command_key`, `idempotency_key`,
`payload_hash`, `account_fingerprint`, `run_nonce` и
`fault=timeout-after-effect`. Bridge сначала применяет один sink effect, затем
удерживает ответ дольше двух секунд или отвечает HTTP 504. Любой обычный ответ
считается отказом сценария.

### `POST /v1/acceptance/lookup`

До появления эффекта допустим только точный ответ:

```json
{"outcome":"UNKNOWN","observed_effects":0}
```

После наблюдения bridge возвращает точные SHA-256 из команды, outcome `APPLIED`
и `observed_effects=1`. Любое другое число эффектов — FAIL.

### `POST /v1/acceptance/cleanup`

После удаления тестового объекта точный ответ:

```json
{"status":"CLEANED"}
```

Cleanup выполняется и после ошибки, если sandbox attestation уже прошла.

## GitHub Environment и secrets

Создать защищённое Environment с точным именем
`v54-live-provider-sandbox`. Добавить в него только:

- `PUW_V54_LIVE_PROVIDER_BASE_URL`;
- `PUW_V54_LIVE_PROVIDER_TOKEN`;
- `PUW_V54_LIVE_PROVIDER_ACCOUNT_FINGERPRINT`;
- `PUW_V54_LIVE_PROVIDER_EXPECTED_HOST_SHA256`.

Последнее значение — SHA-256 нормализованного hostname без схемы, порта и точки
на конце. Account fingerprint — SHA-256 стабильной identity выделенной тестовой
учётной записи. Email, sender, recipient и provider object ID secrets создавать
нельзя.

Перед первым запуском secrets проверяются владельцем sandbox и вторым
рецензентом. Использовать `.env`, production credentials или наследуемые
repository secrets запрещено.

## Запуск

Без эффекта и без secrets:

```powershell
gh workflow run v54-live-provider-acceptance.yml `
  --ref codex/v54-live-provider-gate `
  -f execute_live_sandbox=false
```

Ожидаемый artifact: `status=NOT_RUN`, `observed_effects=0`,
`cleanup=NOT_RUN`.

Фактический тест разрешается только после настройки protected Environment:

```powershell
gh workflow run v54-live-provider-acceptance.yml `
  --ref codex/v54-live-provider-gate `
  -f execute_live_sandbox=true
```

Скачанный `protocol.json` принимается как S10 evidence только если:

- `status=PASS`;
- `timeout_after_effect_observed=true`;
- `dispatch_attempts=1`;
- `reconciliation=PASS`;
- `observed_effects=1`;
- `cleanup=PASS`;
- `raw_output_published=false`.

## Аварийная остановка

Отмена workflow безопасна только вместе с server-side retention bridge. Bridge
обязан автоматически удалить объекты данного `run_nonce` по короткому TTL, даже
если runner завершился до cleanup. После отмены оператор запускает scoped cleanup
на стороне bridge по run nonce; глобальная очистка provider account запрещена.

## Что остаётся для фактического S10 PASS

1. Реализовать/развернуть отдельный bridge к выбранному provider sandbox.
2. Подтвердить технический запрет внешней доставки и TTL cleanup.
3. Создать protected GitHub Environment и четыре тестовых secrets.
4. Выполнить ручной workflow на точном release SHA.
5. Сохранить artifact digest и reviewer approval в release evidence.
