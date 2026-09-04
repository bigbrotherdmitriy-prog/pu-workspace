# Аудит и усиление staging — 2026-09-04

База аудита: `origin/main` на commit `f7aa3f7bcacb33624ff2fa14b91050a6a091dcef`.

## Реализовано без staging-сервера и секретов

- Добавлен отдельный `Staging preflight`: shell syntax, одноразовая конфигурация и валидация реальной Compose-модели.
- Release gate привязан к точным путям четырёх GitHub Actions workflow и принимает только `push`-запуски нужного SHA. Совпадающее имя job из другого workflow больше не является достаточным.
- DNS SSH-host и публичного HTTPS-host независимо проверяется до SSH. Production IP блокируется для обоих; отдельный proxy/CDN/LB разрешён. URL с неправильным или нестандартным TLS-портом отклоняется.
- Запрещён deploy от `root` и на машине с production footprint.
- Закрытый host marker привязывает staging root к Compose project, порту и публичному URL.
- Существующий PostgreSQL volume принимается только с Compose labels выбранного staging project.
- Повторный запуск активного SHA сверяет archive digest, распакованные файлы и revision label Docker image.
- Rollback перестал быть best-effort: прежний Compose обязан запуститься и пройти loopback и public smoke; иначе лог содержит явный `ROLLBACK FAILED`.
- Public smoke входит через выделенную staging-учётную запись и читает основные проектные контуры через HTTPS; пароль передаётся контейнеру через stdin и не попадает в argv или container environment. Проверка также требует `Secure` у session и CSRF cookies; внутренний loopback gateway сохраняет только очищенный внешний `X-Forwarded-Proto: https`.
- Документация требует Environment deployment policy `main` only и независимое подтверждение, если оно доступно на тарифе.

## Что намеренно не выполнялось

- Production не читался и не изменялся.
- В GitHub ничего не публиковалось и не сливалось.
- Публичный staging не создавался: отсутствуют выделенный host, DNS/TLS и SSH credentials.
- Реальные Google/Gmail/Telegram/Gemini/Yandex интеграции в staging не включались.

## Оставшийся внешний контракт

Repository variable:

- `STAGING_ENABLED=true` — только после полной серверной подготовки; до этого `false`.

GitHub Environment `staging` variables:

- `STAGING_HOST`
- `STAGING_USER` — не `root`
- `STAGING_SSH_PORT`
- `STAGING_ROOT`
- `STAGING_PROJECT`
- `STAGING_PORT`
- `STAGING_PUBLIC_URL`

GitHub Environment `staging` secrets:

- `STAGING_SSH_PRIVATE_KEY`
- `STAGING_SSH_KNOWN_HOSTS`

Файл `$STAGING_ROOT/shared/.env.staging`, mode `0400` или `0600`, владелец staging-user:

- `POSTGRES_PASSWORD` (минимум 24 символа)
- `APP_SECRET_KEY` (минимум 32)
- `TOKEN_ENCRYPTION_KEY` (минимум 32)
- `BOOTSTRAP_TOKEN` (минимум 24)
- `PU_SMOKE_PASSWORD` (минимум 20)

Host prerequisites:

- отдельный Linux host без `/opt/pu-workspace/current` и production proxy-файла;
- отдельный non-root user и root, принадлежащий этому пользователю;
- Docker + Compose v2, Python 3, `flock`, `tar`, `sha256sum`, `diff`, `awk` и coreutils;
- host marker из runbook;
- SSH connectivity и проверенный known-host fingerprint;
- DNS и TLS reverse proxy на `127.0.0.1:$STAGING_PORT`;
- outbound доступ для сборки образов и hairpin HTTPS к `STAGING_PUBLIC_URL`;
- внешний мониторинг диска и политика retention backups/releases/images.

Environment protection и branch protection остаются внешними настройками GitHub. В Environment разрешается только protected `main`; branch protection после первого зелёного `staging-preflight` обновляется через `scripts/configure_github_checks.py --apply --sha <SHA>`.

## Выполненные локальные проверки

- Полный backend suite: `515 passed, 2 skipped`.
- Целевые staging/public-smoke contract-тесты: `48 passed, 1 skipped` (Windows не предоставил symlink для одного escape-path теста).
- `sh -n scripts/deploy-staging.sh`, Python compile, YAML parse и `git diff --check`: успешно.
- `scripts/check_ci_security.py`: `passed: true`, замечаний нет.
- `scripts/check_release_package.py`: `ready: true`.
- Независимый read-only аудит подтвердил fail-closed rollback, проверку Compose до SSH, безопасную передачу пароля и обязательный `Secure` для session/CSRF cookies.

Docker на локальной Windows-машине отсутствует, поэтому реальный `docker compose config` здесь не запускался. Добавленный обязательный workflow `Staging preflight` выполняет именно эту проверку на GitHub runner без staging host и секретов; его результат появится только после публикации ветки, которая в рамках этого аудита не выполнялась.
