# PU Workspace v5.4 — Google OAuth reconnect hotfix port

Дата проверки: 2026-09-04

Целевая база: `73d111fb738562f47f5d2ec5ee8f64a92f805770`

Ветка: `codex/v54-oauth-hotfix-port`

## Решение

Выполнен выборочный содержательный перенос Google OAuth reconnect fix. Прямой
`cherry-pick` production-only коммитов не применялся.

Коммиты `6f49b6adf6387f9f1b6e306a3a65d8419879f6ad` и
`4a91d2894a9e81ae04e93f2c11e720faba82cf85` являются sibling-коммитами с общим
родителем `812aee2fc5ae1dcc81e33d6b2de112a0edc093c1`. Более полный `4a91d28`
функционально заменяет `6f49b6a`: он содержит ту же явную проверку обязательных
scope, а также отключает incremental consent и добавляет regression-тест.

Прямой перенос `4a91d28` поверх Wave3 небезопасен: старая версия файла удаляет
актуальные `openid`, provider-bound OAuth state, проверку подписанного OIDC `sub`
и durable mailbox binding. Поэтому перенесены только три актуальных изменения:

1. подавление ошибочного OAuthLib equality-check для scope;
2. fail-closed проверка, что фактически возвращённые scope содержат весь `SCOPES`;
3. reconnect с полным фиксированным набором через
   `include_granted_scopes="false"`.

## Сохранённые границы безопасности

- старт OAuth по-прежнему требует роли `manager` именно в выбранном проекте;
- callback принимает проект только из подписанного, ограниченного по времени и
  привязанного к провайдеру OAuth state;
- обязательный `openid` не удалён;
- Google OIDC `sub` проверяется до изменения credential row;
- mailbox conflict остаётся fail-closed и требует явного revoke;
- access/refresh tokens сохраняются только через существующее шифрование;
- code, tokens, scope values и provider response не добавлены в логи или ошибки;
- при неполном scope возвращается только безопасная ошибка, до OIDC-проверки и
  до создания `GoogleOAuthToken`.

OAuth callback остаётся `GET`, поэтому cookie-CSRF к нему неприменим. Его
anti-CSRF и project-binding механизм — одноразовый nonce внутри подписанного
provider-bound state; существующие tamper/cross-provider тесты сохранены.

## Изменения

- `backend/app/api/google_drive.py`;
- `backend/tests/test_google_oauth_reconnect.py`;
- `backend/tests/test_v54_mailbox_identity.py` (test double приведён к реальному
  контракту `Flow` после явной проверки scope);
- `docs/audits/v54-oauth-hotfix-port.md`.

## Проверки

Команда выполнялась с синтетическими данными и отдельным `basetemp`:

```powershell
python -m pytest <google|yandex|storage|gmail|mailbox|oauth|security tests> -q --basetemp=.pytest-oauth-hotfix
```

Результат: `180 passed in 52.86s`.

В набор вошли Google OAuth reconnect, OAuth state security, mailbox identity и
rollout controls, Gmail, Google Calendar/Tasks, Google/Яндекс storage adapters,
привязка storage и security headers. `git diff --check` выполняется отдельно
перед коммитом.

## Ограничения

- живой Google OAuth не вызывался;
- реальные credentials, client data и production БД не использовались;
- PostgreSQL concurrency к этому узкому изменению не относится и не запускался;
- production не изменялся;
- push, merge и deploy не выполнялись.
