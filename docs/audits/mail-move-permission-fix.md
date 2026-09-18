# Gmail: корзина и спам — недостающее разрешение

Дата: 2026-09-05. База: `59e6d5a1466007d079d9c2e002fdaeca69d07bcb`.
Ветка: `codex/pu-36-mail-move-permission-fix`.

## Подтверждённый дефект

OAuth SCOPES запрашивали Gmail readonly/send, но не modify. Реализованные
trash/modify операции требуют gmail.modify (либо более широкого mail.google.com).
Источник: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/trash

Скриншот пользователя показывает confirmation, но не ответ API. Фактические
production scopes и ответ Gmail не читались: дефект кода подтверждён, связь с
конкретным production-запросом ещё требует проверки после выпуска.

Дополнительно создание Gmail service находилось вне try и ошибочно попадало в
ветку unknown outcome даже до отправки запроса. Исправлено безопасным отказом.

## Изменения

- Добавлен gmail.modify в существующий OAuth consent. Другие разрешения сохранены.
- Перед move проверяются сохранённые scopes; при отсутствии права нет provider I/O
  и изменения локальных labels. HTTP 403 с фиксированным безопасным кодом.
- Корзина использует recoverable trash, не permanent delete.
- UI показывает рядом с почтой инструкцию переподключить Google, сохраняя письмо.
- Обновлён synthetic consent fixture: подтверждается сохранение refresh token,
  шифрование и новый granted scope. Проверка неполного consent не ослаблена.
- Итоговый frontend bundle пересобран; v9 и предыдущие mail/MPP fixes сохранены.

## Проверки

- До исправления: 6 новых adapter/OAuth regressions FAIL, 4 существующих PASS.
- После: 58 backend tests PASS (mailbox adapter, mail API, OAuth reconnect,
  Google workspace acceptance, Gmail adapter), SQLite/synthetic fixtures.
- Полный frontend suite: 189 PASS; TypeScript check и build PASS.
- Chromium v9/mail integration: 2 PASS, mock API. Это не тест реального удаления.
- Проверены spam/trash и исчезновение из локального inbox; отсутствие provider
  вызова без modify scope; запрет изменения labels при отказе; безопасная ошибка
  setup; поддержка точного modify и ранее выданного полного scope.
- Полный backend/PostgreSQL/production smoke не запускались. Предупреждение build
  о JS chunk > 500 kB сохраняется.

## После выпуска

Сначала CI точного release SHA и согласование с параллельным владельцем production.
Затем пользователь повторно подключает Google того же проекта/аккаунта через
«Интеграции» и подтверждает новый consent. Не удалять старую привязку/проекты и не
подставлять другой аккаунт. Само обновление кода права старого токена не повышает.
Google consent screen/verification для нового restricted scope может потребовать
настройки владельцем Google Cloud; обходить ограничения нельзя.

В этой работе нет push/deploy, реальных почтовых операций и изменения credentials.
