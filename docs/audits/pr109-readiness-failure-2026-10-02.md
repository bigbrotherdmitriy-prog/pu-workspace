# PR #109: отказ readiness при выкатке 01.10.2026

Проверено 02.10.2026 read-only. Candidate повторно не запускался; cutover,
миграции, записи БД и изменения AUTO не выполнялись. Production остался на
сохранённом прежнем комплекте.

## Наблюдаемый отказ

Candidate: merge `ef96775f17932494255f3869b7091c6032395c0a`, image
`sha256:0b62c8d774eab25a079e2b3dcf2ac7379e062569b9775fe3b9cf0989d8f6bc11`.

| Событие 01.10, UTC | Результат |
|---|---|
| 18:07:21.984383 — запись candidate receipt завершена | 56 файлов образа через loopback: HTTP 200, размеры и SHA-256 совпали |
| 18:07:26.519634 — Caddy access log `/api/status` | HTTP 200; 111 байт |
| 18:07:26.591887 — Caddy access log `/api/readiness` | HTTP 200; 1069 байт; verifier остановился: `public readiness is not ready:true` |
| Публичные файлы candidate | Ни одного запроса в отказавшем вызове verifier; 56-файловая HTTPS-сверка не началась |
| 18:07:28.680688, 18:07:30.008266 — DB heartbeat candidate worker | Первые регистрации двух worker, уже после публичной проверки |
| 18:07:59.215719 — rollback receipt завершён | Exact прежний образ, четыре компонента, readiness true, все 55 публичных файлов совпали |

Access-log timestamps — время логирования завершённого запроса, а не точные
метки переключения симлинка. Точное время atomic cutover не сохранено. От записи
успешного loopback receipt до записи публичной readiness прошло **4,607504 с**;
это не измерение интервала cutover → HTTP. Filename rollback `20261001T180727Z`
обозначает секунду 27.xxx, а не точное время 27.000.

Источники: retained deploy log и receipts в
`/opt/pu-workspace-primary/secure-validation/pr109-ef96775f17932494255f3869b7091c6032395c0a`
и `/opt/pu-workspace-primary/static-receipts/`; Caddy access log
`/var/log/caddy/pu-workspace-primary-access.log`. Сверены SHA-256 локальных копий
receipts с серверными. Исполненный verifier совпадает с Git blob, кроме CRLF:
LF-normalized SHA-256 `3b937806d3212611d610244a722a1cefdd20c3cfa2fa472e6d8930355ac04a78`.

## Почему это остановка до проверки байтов

[Порядок verifier на exact merge](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/ef96775f17932494255f3869b7091c6032395c0a/scripts/verify_image_static.py#L217):
components → public status/readiness → image extraction → static HTTP. Ошибка
возникла в readiness, до `verify_http()`. До неё оба API-ответа прошли HTTP 200,
проверку отсутствия redirect, JSON parsing и matching release из `/api/status`.

[Readiness](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/ef96775f17932494255f3869b7091c6032395c0a/backend/app/core/readiness.py#L95)
требует ≥2 worker и ≥1 scheduler с heartbeat за последние 90 секунд.
`docker compose up --wait` не гарантирует регистрацию worker: у этих сервисов
нет отдельного heartbeat healthcheck. Loopback smoke до включения фоновых сервисов
намеренно допускает отсутствие их heartbeat; его успех не означает публичную
readiness.

Production SQL запускался только с
`PGOPTIONS='-c default_transaction_read_only=on'`:

```sql
SELECT service_kind, service_id, started_at, last_seen
FROM service_heartbeats
WHERE service_id LIKE 'ee17bd9ebd08-%' OR service_id LIKE '283070b0ed69-%'
ORDER BY started_at;

SELECT count(*) AS eligible_worker_rows_at_logged_readiness_time
FROM service_heartbeats
WHERE service_kind='worker'
  AND started_at <= '2026-10-01T18:07:26.591887Z'
  AND last_seen >= '2026-10-01T18:05:56.591887Z';
-- 0
```

Полное failed readiness body не сохранено. Поэтому остальные одновременные
required failures и длительность внутренней инициализации worker неизвестны.
Установлена преждевременная проверка heartbeat quorum, но **не доказано
отсутствие исторического расхождения байтов candidate**: эти байты по публичному
HTTPS тогда не измерялись. Запрос к readiness дошёл; это не transport timeout.

## Публичный путь и повторная проверка

Текущий путь: DNS A `72.56.108.162`, AAAA отсутствует → Caddy на 443 с TLS →
`reverse_proxy 127.0.0.1:3020` → Docker/backend. Активный route для
`puworkspace.ru` не разделяет `/api/status`, `/api/readiness`, `/new/` по
upstream; cache handler отсутствует. В проверенных ответах нет `Age`, `Via`,
`X-Cache`; признаков CDN в этой конфигурации не найдено. Это описание проверенного
текущего route, не доказательство неизменности всей исторической инфраструктуры.

Не следует объявлять весь публичный сервис непрерывно доступным: в журнале
cutover/rollback есть **7 HTTP 502 других API-запросов** к тому же upstream.
`processing-queue`: 18:07:07.011406 (connection refused), 18:07:11.006811,
18:07:15.004946, 18:07:43.007152 (connection reset), 18:07:47.004977 (EOF).
`mobile-sync`: 18:07:47.137122 и 18:07:47.449756 (connection reset).
Это реальные transport-отказы во время замены приложения, не несовпадение
статических байтов. Они не были ответами двух readiness/status запросов verifier:
те вернули 200. Историческая успешная статика с User-Agent
`PU-Static-Integrity/1` относится уже к rollback: 55 GET в
18:07:57.343794–18:07:59.214315, каждый HTTP 200.

02.10.2026 03:25:04–03:25:06 UTC выполнен полный manual verifier с retained
прежним exact image, `--legacy-image --compose-project puw-primary-next` и
`--base-url https://puworkspace.ru`. Exit 0: status HTTP 200, readiness true,
все required checks зелёные, все **55 файлов HTTP 200**, размеры и SHA-256
совпали. Production SHA `6ee151fd4f97050488c7b10283efe503e3d2f7ee`; image
`sha256:30d8d99578bd553595c80e0b2692c2473c3c1610686d8306a8dc32bd2d47e8d8`.
Manifest inventory SHA-256 `f4c2be9b16adc92c2e3a40d42483135ec111d77efb0e98c39eed023f8110e5ea`.

Manual receipt:
`/opt/pu-workspace-primary/secure-validation/pr109-public-replay-20261002T0325Z/current-production-https.json`;
SHA-256 `87e6f066156dfd3a007f1c482304f3033a61a6f3b01ed4afd41e2ecfa4b308c3`.
Это приёмка **текущего прежнего** образа, не candidate #109.

## Граница исправления

Разрешены ограниченное ожидание heartbeat и сохранение диагностики. Байтовые,
TLS, HTTP, revision, image, component и полный manifest gates не ослабляются.
Partial static verification остаётся отказом; byte mismatch не повторяется и
не считается startup race. Новая реальная выкатка требует отдельного решения.
Удаление `react_dist` из Git и бюджетная реализация остаются после successful
production acceptance первого этапа.
