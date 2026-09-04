# Проверка и тестовый стенд PU Workspace

Рабочая ветка настройки: `codex/automation-setup`. Production не обновляется этим процессом.

## CI

На каждый PR и после push в `main` запускаются CI, Docker smoke и security gates. Docker smoke собирает настоящий frontend, поднимает отдельную PostgreSQL, выполняет миграции, запускает API, два worker и scheduler. Проверяет авторизацию, локальную загрузку вложенного документа, изоляцию проектов, UI вход, перезапуск процессов и восстановление резервной копии во вторую базу. В конце удаляет только ресурсы собственного Compose project.

Секреты для тестов генерируются заново. Production .env, OAuth, Gmail и файлы Google Drive не используются. Логи очищаются перед публикацией. Dependency audit блокирует проверки при обнаружении уязвимостей; исключения автоматически не добавляются. Локальный secret scan покрывает высокодостоверные форматы, но не заменяет полноценный исторический secret scan.

## Локальный staging

Из этой копии репозитория, где доступен Docker Compose v2:

```powershell
python scripts/prepare_test_environment.py --output .env.staging --port 3010
docker compose --env-file .env.staging -f docker-compose.ci.yml -p puw-staging up -d --build --wait --wait-timeout 180
python scripts/check_ci_smoke.py --env-file .env.staging --seed
```

Адрес: http://localhost:3010/new/. Тестовая учётная запись: ci-admin@example.test; пароль находится только в .env.staging. Повторный запуск проверки: без --seed. Генератор отказывается перезаписывать существующий файл. Для новой версии создайте новый стенд с отдельным project name, файлом окружения и портом; не подменяйте версию под существующей базой.

Это локальный стенд. Для Cloud Browser требуется отдельный HTTPS-адрес, тестовый аккаунт и авторизация пользователя. Не направляйте Cloud Browser на production для загрузки тестовых документов. Для настоящих Google Workspace проверок подключайте отдельный тестовый Google-аккаунт; текущий стенд специально изолирован от внешней сети.

## Публичный staging

Workflow `Deploy staging` разворачивает только отдельный Compose project из `docker-compose.ci.yml`. Он запускается после успешного `Docker smoke` для `main` или вручную, но остаётся пропущенным, пока переменная `STAGING_ENABLED` не равна `true`. Перед SSH он проверяет все пять release gates для точного commit SHA и убеждается, что SHA всё ещё является вершиной `main`. Concurrency lock в GitHub и `flock` на сервере не допускают двух одновременных переключений.

Staging использует отдельный сервер, собственную PostgreSQL volume, собственные случайные ключи, отдельный loopback-порт и отдельный HTTPS-домен. Google, Gmail, Telegram, Gemini, Yandex и фоновые внешние автоматизации принудительно отключаются. Production host `37.252.23.204`, production compose, `/opt/pu-workspace`, порт 3000 и production-домены отвергаются до изменения контейнеров. Rendered Compose дополнительно проверяется перед сборкой: фиксированные имена контейнеров, host namespaces, privileged/capabilities/devices, посторонние mounts/networks/images и публикация не на loopback блокируют выпуск.

Один раз на выделенном staging-сервере создайте закрытый файл. Генератор не перезаписывает существующий файл:

```bash
sudo install -d -m 700 -o puw_staging -g puw_staging /opt/pu-workspace-staging/shared
sudo -u puw_staging git clone --depth 1 https://github.com/bigbrotherdmitriy-prog/pu-workspace.git /tmp/puw-staging-bootstrap
sudo -u puw_staging python3 /tmp/puw-staging-bootstrap/scripts/prepare_test_environment.py \
  --output /opt/pu-workspace-staging/shared/.env.staging --port 3010
```

Используйте отдельный сервер, на котором нет production-контейнеров и production-секретов. Обычный доступ к системному Docker daemon практически равен root-доступу ко всему серверу, поэтому одной отдельной папки на production-машине недостаточно. Пользователь `puw_staging` должен иметь Docker-доступ и владеть только `/opt/pu-workspace-staging`; предпочтителен rootless Docker. SSH-ключ не должен использоваться production-деплоем. На reverse proxy добавьте отдельный host, например `staging.example.test`, направленный на `127.0.0.1:3010`, и выпустите для него TLS-сертификат. DNS этого host должен указывать на staging-сервер до первого запуска workflow.

В GitHub создайте Environment с точным именем `staging`. На уровне репозитория добавьте управляющую переменную `STAGING_ENABLED`: сначала `false`, после DNS/серверной подготовки — `true`. Она намеренно является repository variable, чтобы GitHub мог решить, запускать ли job, до выдачи доступа к Environment.

В Environment `staging` добавьте переменные:

- `STAGING_HOST`: отдельный SSH hostname или IP staging-сервера;
- `STAGING_USER`: выделенный пользователь, например `puw_staging`;
- `STAGING_SSH_PORT`: SSH-порт, обычно `22`;
- `STAGING_ROOT`: отдельный корень, например `/opt/pu-workspace-staging`;
- `STAGING_PROJECT`: отдельное имя Compose project, например `puw-staging`;
- `STAGING_PORT`: отдельный loopback-порт, например `3010`;
- `STAGING_PUBLIC_URL`: отдельный HTTPS origin без пути, например `https://staging.example.test`.

Добавьте два секрета Environment:

- `STAGING_SSH_PRIVATE_KEY`: закрытый ключ только staging-пользователя;
- `STAGING_SSH_KNOWN_HOSTS`: заранее проверенная строка host key из `ssh-keyscan`, сверенная по fingerprint через панель хостинга/консоль, а не полученная вслепую во время workflow.

После этого вручную запустите `Deploy staging` один раз из ветки `main`. Первый запуск создаёт только staging-базу и тестовую учётную запись `ci-admin@example.test`. Пароль остаётся в `/opt/pu-workspace-staging/shared/.env.staging`. Каждый следующий выпуск делает и проверяет PostgreSQL backup, сохраняет прежний image/release, ждёт readiness, выполняет loopback smoke и public HTTPS smoke. При совместимой схеме неуспешная проверка возвращает прежнее приложение; backup базы автоматически не затирается и не восстанавливается поверх живой базы.

## Linear и выпуск

Для Linear требуется привязанный аккаунт, установленная интеграция Codex for Linear и облачное окружение репозитория `bigbrotherdmitriy-prog/pu-workspace`. Установка этих внешних компонентов не подтверждается файлами репозитория.

Короткий операционный сценарий, правила именования и локальная проверка описаны в [`LINEAR_CODEX_RUNBOOK_RU.md`](LINEAR_CODEX_RUNBOOK_RU.md).

Проверяемый порядок работы:

1. Создать реальную задачу в workspace `pu-workspace-ai`, указать репозиторий, границы изменения и критерии готовности.
2. Запустить Codex из этой задачи. Ветка должна называться `codex/pu-N-краткое-описание`.
3. Открыть PR с заголовком `[PU-N] Краткое описание` и канонической ссылкой `https://linear.app/pu-workspace-ai/issue/PU-N/...` в поле `Linear` шаблона PR.
4. Обязательный job `package-and-secrets` запускает `scripts/check_pr_linear_link.py`. Он проверяет, что во всех трёх местах указан один и тот же ключ одной задачи. Проверка работает только с GitHub event payload, не читает токены и не заявляет, что задача существует в Linear.
5. Сливать PR только после обязательных CI checks и проверки тестового окружения. Затем убедиться в Linear, что GitHub-интеграция привязала PR и перевела задачу в ожидаемый статус; закрывать задачу только после этой проверки.

Полная приёмка цепочки выполняется один раз на отдельной тестовой задаче: `Issue -> запуск Codex -> PR -> обязательный CI -> тестовый сайт -> Done`. Если PR прошёл репозиторный валидатор, но не появился в Linear или статус не изменился, это внешний сбой интеграции, а не основание закрывать задачу вручную без проверки.

Ветка main должна требовать проверки `test-and-build`, `docker-smoke`, `package-and-secrets`, `python-dependencies`, `frontend-dependencies` перед слиянием. `python scripts/configure_github_checks.py` читает текущие правила через существующее подключение Git Credential Manager. Параметры `--apply --sha <40-символьный SHA>` добавляют обязательные проверки только после успешных GitHub Actions именно этого коммита; существующие правила сохраняются. Настройка облачного staging проверяется отдельно; данный файл не означает, что публичный тестовый сервер уже развёрнут.

Для подключения Linear на этом компьютере добавлен официальный MCP сервер. Авторизация завершается пользователем через `codex mcp login linear`; она не заменяет установку Codex for Linear в рабочем пространстве Linear.

Автоматический контроль GitHub/сайта настраивается через автоматизации Codex, без чтения личных писем. Уведомления только о новых сбоях, восстановлении и необходимых решениях.
