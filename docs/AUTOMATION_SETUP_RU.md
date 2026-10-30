# Проверки и автоматизация PU Workspace

Production не обновляется CI-процессом из этого документа. Официальные production-скрипты и конфигурация описаны отдельно и не зависят от тестового Docker Compose.

## CI

На каждый PR и после push в `main` запускаются CI, Docker smoke и security gates. Docker smoke собирает настоящий frontend, поднимает одноразовую PostgreSQL, выполняет миграции, запускает API, два worker и scheduler. Он проверяет авторизацию, локальную загрузку вложенного документа, изоляцию проектов, UI-вход, перезапуск процессов и восстановление резервной копии во вторую базу. В конце удаляются только ресурсы собственного Compose project.

Секреты для тестов генерируются заново. Production `.env`, OAuth, Gmail и файлы Google Drive не используются. Логи очищаются перед публикацией. Dependency audit блокирует проверки при обнаружении уязвимостей; исключения автоматически не добавляются. Локальный secret scan покрывает высокодостоверные форматы, но не заменяет полноценный исторический secret scan.

## Локальная одноразовая CI-среда

Из копии репозитория, где доступен Docker Compose v2:

```powershell
python scripts/prepare_test_environment.py --output .env.ci --port 3010
docker compose --env-file .env.ci -f docker-compose.ci.yml -f docker-compose.ci-upload.yml -p puw-ci-local up -d --build --wait --wait-timeout 180
python scripts/check_ci_smoke.py --env-file .env.ci --seed
```

Адрес: `http://localhost:3010/new/`. Тестовая учётная запись — `ci-admin@example.test`; пароль находится только в `.env.ci`. Повторный smoke запускается без `--seed`. Генератор отказывается перезаписывать существующий файл. Для новой проверки создайте новую одноразовую среду с отдельными Compose project, файлом окружения и портом.

Это локальная CI-среда, а не публичный staging. Для настоящих Google Workspace проверок используйте отдельное защищённое test-only окружение и тестовый Google-аккаунт. Не направляйте синтетические загрузки на production.

### Ошибка `STAGING_HOST resolves to the production host`

После перевода прежнего EU staging-сервера в production работа старого тестового URL или уже запущенных контейнеров не означает, что безопасный независимый автодеплой staging готов. [`validate_staging_settings.py`](../scripts/validate_staging_settings.py) намеренно останавливает preflight, если `STAGING_HOST` разрешается в production-адрес. Совпадение DNS или использование общего с production Docker daemon либо deploy-пользователя не позволяют независимо обновлять стенды: такой staging нужно считать production, а для staging подготовить отдельный хост по правилам выше. Guard не отключайте и не обходите; [`deploy-staging.sh`](../scripts/deploy-staging.sh) также требует выделенный хост и блокирует production footprint.

Проверьте DNS с рабочей станции, подставив только публичные имена staging и production (команды ничего не изменяют):

```bash
getent ahosts staging.example.test | awk '{print $1}' | sort -u
getent ahosts puworkspace.ru | awk '{print $1}' | sort -u
```

Если адреса пересекаются, сначала выделите отдельный staging-сервер и исправьте DNS штатным способом, затем дождитесь обновления DNS и повторите preflight. На предполагаемом выделенном сервере можно только прочитать имя активного релиза; команда ниже не выводит `.env`, marker или секреты:

```bash
ssh puw_staging@staging.example.test '
current=/opt/pu-workspace-staging/current
if ! test -L "$current"; then
  echo "current отсутствует или не является символической ссылкой" >&2
  exit 1
fi
target=$(readlink -f "$current") || target=
if ! test -d "$target"; then
  echo "current отсутствует или ссылка повреждена" >&2
  exit 1
fi
case "$target" in
  /opt/pu-workspace-staging/releases/*) basename "$target" ;;
  *) echo "current указывает вне каталога staging releases" >&2; exit 1 ;;
esac'
```

Отсутствующий или повреждённый `current` означает, что на этом хосте нет подтверждённого активного staging-релиза. Не запускайте deploy для проверки и не подключайте staging workflow к старому production-хосту.

## Linear и выпуск

Для Linear требуется привязанный аккаунт, установленная интеграция Codex for Linear и облачное окружение репозитория `bigbrotherdmitriy-prog/pu-workspace`. Установка этих внешних компонентов не подтверждается файлами репозитория.

Короткий операционный сценарий, правила именования и локальная проверка описаны в [`LINEAR_CODEX_RUNBOOK_RU.md`](LINEAR_CODEX_RUNBOOK_RU.md).

Проверяемый порядок работы:

1. Создать реальную задачу в workspace `pu-workspace-ai`, указать репозиторий, границы изменения и критерии готовности.
2. Запустить Codex из этой задачи. Ветка должна называться `codex/pu-N-краткое-описание`.
3. Открыть PR с заголовком `[PU-N] Краткое описание` и канонической ссылкой `https://linear.app/pu-workspace-ai/issue/PU-N/...` в поле `Linear` шаблона PR.
4. Job `package-and-secrets` запускает warning-only проверку Linear linkage и обязательные secret/release-package проверки.
5. Сливать PR только после обязательных CI checks. Production deploy выполняется отдельной подтверждённой процедурой на точном SHA.

Ветка `main` должна требовать проверки `test-and-build`, `docker-smoke`, `package-and-secrets`, `python-dependencies`, `frontend-dependencies`. `python scripts/configure_github_checks.py` читает текущие правила через существующее подключение Git Credential Manager. Параметры `--apply --sha <40-символьный SHA>` добавляют обязательные проверки только после успешных GitHub Actions именно этого коммита; существующие правила сохраняются.

Автоматический контроль GitHub/сайта настраивается через автоматизации Codex, без чтения личных писем. Уведомления нужны только о новых сбоях, восстановлении и необходимых решениях.
