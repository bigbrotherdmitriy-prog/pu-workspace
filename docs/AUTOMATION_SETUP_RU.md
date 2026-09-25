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
