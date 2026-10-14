# Мониторинг продакшена PU Workspace

Цель: владелец узнаёт о сбое из Telegram, а не случайно. Мониторинг состоит из двух
независимых частей:

| Часть | Где работает | Что ловит |
|---|---|---|
| **Uptime** (`.github/workflows/uptime.yml`) | GitHub Actions, каждые 15 мин | сайт недоступен снаружи: упал хост, сеть, Caddy, backend |
| **Host watchdog** (`scripts/monitoring/host_watchdog.sh`) | systemd-таймер на прод-хосте, каждые 5 мин | проблемы внутри хоста: диск, память, контейнеры, очередь, Gmail-синхронизация |

Если хост целиком выключен, watchdog молчит — это поймает Uptime. Если хост жив, но
очередь встала, сайт снаружи может отвечать 200 — это поймает watchdog.

## 1. Uptime (GitHub Actions)

Проверяет, что отвечают HTTP 200:

- `https://puworkspace.ru/new/` — интерфейс;
- `https://puworkspace.ru/health` — health-эндпоинт backend (FastAPI `/health`, проксируется Caddy).

Сбой засчитывается только если проверка не прошла **два раза подряд с интервалом 60 с**.
Тогда:

- открывается одна issue с меткой `outage` (если уже открыта — добавляется комментарий);
- в Telegram уходит сообщение «🔴 PU Workspace недоступен», затем напоминание примерно раз в 2 часа;
- когда сайт снова отвечает — issue закрывается с комментарием, в Telegram уходит «🟢 восстановлен».

Секреты репозитория (Settings → Secrets and variables → Actions):

- `TELEGRAM_BOT_TOKEN` — токен бота (тот же, что `TELEGRAM_BOT_TOKEN` в прод-env);
- `TELEGRAM_CHAT_ID` — чат для алертов (тот же, что `TELEGRAM_CHAT_ID` в прод-env).

Без секретов workflow всё равно ведёт issue, но Telegram пропускает (warning в логе).
Ручной запуск: Actions → Uptime → Run workflow.

**Как заглушить:** Actions → Uptime → «…» → Disable workflow (включить — Enable).
Учтите: GitHub сам отключает расписания в репозитории без активности 60 дней — тогда
придёт письмо, workflow нужно включить снова.

## 2. Host watchdog (прод-хост)

Что проверяется (каждые 5 минут):

| Ключ | Проверка | Порог |
|---|---|---|
| `disk` | заполнение `/` и `/mnt/data` | ≥ 80 % |
| `memory` | `MemAvailable` из `/proc/meminfo` | < 10 % от `MemTotal` |
| `containers` | все `puw-primary-next-*`: запущены и не `unhealthy`; обязательно есть backend, db, scheduler-1, хотя бы один worker, telegram-relay | любой сбой |
| `heartbeat` | самый свежий `service_heartbeats.last_seen` для `worker` и для `scheduler` (пишет `app.jobs.queue.touch_service`) | старше 5 мин |
| `gmail_sync` | последний `background_jobs` с `kind='gmail.sync'` и `status='completed'` | старше 30 мин (если Gmail-синхронизация ни разу не завершалась — не проверяется) |
| `backend_health` | `http://127.0.0.1:3020/health` | не 200 |

Скрипт только читает: `statvfs`, `/proc/meminfo`, `docker ps`, SQL в транзакции
`READ ONLY` через `docker exec puw-primary-next-primary-db psql`, HTTP GET health.

**Куда уходят алерты.** В Telegram, в чат `TELEGRAM_CHAT_ID` из прод-env:

1. основной путь — `docker exec puw-primary-next-primary-backend python -c "...notify_telegram(...)"`
   (тот же нотификатор и relay, что использует приложение);
2. если backend-контейнер не запущен или отправка не удалась — напрямую: скрипт сам читает
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (и `TELEGRAM_RELAY_URL`/`TELEGRAM_RELAY_SECRET`, если есть)
   из `/opt/pu-workspace-primary/shared/.env.primary` в момент отправки и шлёт через relay
   или Bot API. Значения никогда не печатаются.

**Дедупликация.** По каждой проблеме — одно сообщение, повтор не чаще раза в 6 часов, пока
проблема не исчезла. Когда проблема исчезла — одно сообщение «🟢 восстановлено». Если отправка
не удалась, алерт будет повторён на следующем запуске. Состояние: `/var/lib/pu-watchdog/state.json`.

### Команды

```sh
W=/usr/local/lib/pu-watchdog/host_watchdog.sh
sudo $W --dry-run          # выполнить проверки и показать, что было бы отправлено; ничего не пишет и не шлёт
sudo $W --test-alert       # отправить тестовое сообщение в Telegram
sudo $W --silence 6h       # заглушить алерты на 6 часов (30m, 2d, ...)
sudo $W --unsilence        # снять заглушку
journalctl -u pu-watchdog.service -n 50   # последние запуски
```

Во время заглушки проверки идут, но сообщения не шлются; нерешённые проблемы будут
отправлены сразу после окончания заглушки. Бессрочно заглушить: `sudo systemctl stop pu-watchdog.timer`.
Отключить отдельную проверку: `Environment=WATCHDOG_DISABLE=gmail_sync,memory` в override
(`sudo systemctl edit pu-watchdog.service`). Ключи — из таблицы выше.

Прочие переменные (для override): `WATCHDOG_HEALTH_URL`, `WATCHDOG_ENV_FILE`,
`WATCHDOG_EXPECTED_CONTAINERS`, `WATCHDOG_HOST_LABEL`, `WATCHDOG_STATE_DIR`.

### Установка (оператор, один раз)

Скрипт копируется из `main`, а не запускается из git-checkout, чтобы переключение веток не
меняло мониторинг.

```sh
# выгрузить scripts/monitoring из origin/main во временный каталог, не трогая рабочие checkout'ы
git -C /opt/pu-workspace-dev fetch origin
rm -rf /tmp/pu-watchdog-src && mkdir /tmp/pu-watchdog-src
git -C /opt/pu-workspace-dev archive origin/main scripts/monitoring | tar -x -C /tmp/pu-watchdog-src
cd /tmp/pu-watchdog-src
sudo install -d -m 0755 /usr/local/lib/pu-watchdog
sudo install -m 0755 scripts/monitoring/host_watchdog.sh scripts/monitoring/host_watchdog.py /usr/local/lib/pu-watchdog/
sudo install -m 0644 scripts/monitoring/systemd/pu-watchdog.service scripts/monitoring/systemd/pu-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo /usr/local/lib/pu-watchdog/host_watchdog.sh --dry-run     # проверить
sudo /usr/local/lib/pu-watchdog/host_watchdog.sh --test-alert  # убедиться, что Telegram доходит
sudo systemctl enable --now pu-watchdog.timer
systemctl list-timers pu-watchdog.timer
```

Обновление — повторить `install` двух файлов скрипта. Удаление:
`sudo systemctl disable --now pu-watchdog.timer && sudo rm /etc/systemd/system/pu-watchdog.* && sudo rm -r /usr/local/lib/pu-watchdog /var/lib/pu-watchdog`.

Тесты логики порогов и дедупликации: `backend/tests/test_host_watchdog.py`.
