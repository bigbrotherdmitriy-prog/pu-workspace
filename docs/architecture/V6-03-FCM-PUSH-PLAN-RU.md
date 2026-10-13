# V6-03: FCM preflight и план реализации push

*Черновик подготовил Gemini по ТЗ (раздел 42) и коду; проверил и поправил Claude 13.10.2026: адреса, передача токена, домен Android-приложения. Статус: **утверждён владельцем 13.10.2026** («План утверждаю»).*

## 1. Коротко для владельца
* **Что вы получите:** На телефон с Android-клиентом начнут приходить системные уведомления по ключевым событиям проекта (назначение задачи, приближение дедлайна, эскалация просрочки, упоминания и системные алерты).
* **Чего НЕ будет в тексте push:** Согласно разделу 16 и 42 ТЗ, пуши конфиденциальны: в них **нет** денежных сумм, цен, фрагментов договоров и текста актов. Приходит только краткий заголовок события и внутренняя ссылка.
* **Что требуется от вас:** Только утвердить настоящий документ и архитектурный план. Все настройки Firebase и ключи уже подготовлены на сервере.

---

## 2. Preflight (текущее состояние)
* **Firebase-проект и ключи:** Проект `pu-workspace-57c66`, приложение `ru.puworkspace.app`. Файлы `google-services.json` и приватный ключ сервисного аккаунта лежат на сервере вне git-репозитория; вызов Google OAuth2 / FCM HTTP v1 проверен.
* **Android-клиент:** В репозитории (`android/app/`) находится TWA на базе `com.google.androidbrowserhelper:androidbrowserhelper:2.5.0` с `LauncherActivity`. Firebase SDK, плагин `google-services`, класс `FirebaseMessagingService` и нативные обработчики push **отсутствуют**.
* **Подсистема уведомлений MVP-3:** В бэкенде работают модули:
  * `backend/app/models/management.py` (`Notification`, `Obligation`)
  * `backend/app/notification_escalation.py` (правила эскалаций, тихие часы `outside_quiet_hours`)
  * `backend/app/automations/notifications.py` (фоновое обновление)
  * `backend/app/telegram_relay.py` и `backend/app/integrations/telegram.py` (канал Telegram)
* **Durable outbox:** В схеме уже есть таблица `background_jobs` (используется `v54_provider_dispatch_outbox`). Полноценной очереди отправки push-уведомлений пока нет — она будет создана с опорой на `background_jobs`.
* **Пользователь для токена:** В кодовой базе есть модель `User` (`backend/app/models/user.py`), аутентификация `require_user` (`backend/app/core/auth.py`), а в PWA/TWA — сессия пользователя.

---

## 3. Архитектура решения

```
[Событие MVP-3] ──> [PushChannelAdapter (новое)] ──> [Таблица push_outbox (новое)]
                                                              │
                                                     [background_jobs]
                                                              │
[FCM HTTP v1 API] <───────────────────────────────────────────┘
       │
       ▼
[Android FirebaseMessagingService (новое)] ──> [Native Notification] ──> Клик (Deep Link в TWA)
```

1. **PushChannelAdapter (новое):** Модуль `backend/app/integrations/push.py` по аналогии с `backend/app/integrations/telegram.py`. Принимает события от генератора уведомлений MVP-3.
2. **Таблица токенов `user_device_tokens` (новое):**
   * Поля: `id` (int, PK), `user_id` (FK `users.id`, ondelete CASCADE, index), `device_id` (str, 100), `fcm_token` (text, unique), `platform` (str, default 'android'), `created_at` (timestamptz), `updated_at` (timestamptz).
3. **API управления токенами (новое):**
   * `POST /api/notifications/devices/register` — привязка FCM-токена к текущему `user_id` (через `require_user`).
   * `POST /api/notifications/devices/revoke` — отзыв токена при логауте.
4. **Durable outbox и фоновая отправка:**
   * Таблица `push_outbox` (новое) со статусами `pending`, `sent`, `failed`, `dead`. Задача ставится транзакционно в едином коммите с генерацией уведомления.
   * Обработка через существующий диспетчер `background_jobs` с экспоненциальным backoff (3 попытки). Сбой FCM не приводит к потере уведомления.
   * Инвалидация токенов: При ответе FCM HTTP v1 с ошибками `UNREGISTERED` или `INVALID_ARGUMENT` токен немедленно удаляется из `user_device_tokens`.
5. **Payload и безопасность:**
   * Секреты Firebase — строго через переменные окружения (`FCM_SERVICE_ACCOUNT_JSON_PATH` или `FCM_CREDENTIALS_JSON`). Readiness-проверка (`/health` или readiness probe) блокирует старт при их повреждении.
   * Формат payload: `{"title": "PU Workspace: Задача обновлена", "body": "Новое событие по задаче #123", "data": {"deep_link": "https://puworkspace.ru/new/?task=123"}}`. Без сумм и выдержек из документов!
6. **Android & TWA:**
   * Подключение `com.google.gms:google-services` и `com.google.firebase:firebase-messaging:24.1.0`.
   * Реализация `PuFirebaseMessagingService (новое)`: получает push в фоне/foreground, поднимает системную нотификацию в канале `pu_workspace_alerts` с `PendingIntent` на `LauncherActivity` с URL страницы.
   * **Передача токена в веб-сессию (решение):** у TWA нет общего cookie с нативным кодом, поэтому токен передаётся через веб-приложение. Нативный `PuFirebaseMessagingService.onNewToken` сохраняет токен локально; `LauncherActivity` (новый подкласс) при каждом запуске добавляет его во **фрагмент** стартового URL — `https://puworkspace.ru/new/#fcm=<token>` (фрагмент не уходит на сервер и в логи). SPA читает фрагмент, вызывает `POST /api/notifications/devices/register` в своей авторизованной сессии и сразу убирает фрагмент из адреса (`history.replaceState`). При выходе SPA вызывает `/devices/revoke`.
   * **Домен приложения (новое требование preflight):** манифест Android сейчас открывает старый адрес `pu-workspace.duckdns.org/new/`. В рамках PR 3 перевести `DEFAULT_URL`, `intent-filter host` и Digital Asset Links (`/.well-known/assetlinks.json` на `puworkspace.ru`) на `puworkspace.ru`; старый адрес оставить рабочим до обновления APK у пользователей.

---

## 4. События первой версии, лимиты и тихие часы
* **События (типы из `management.py` и `notification_escalation.py`):**
  1. `task_assigned` / `task.update` (назначение исполнителя, смена статуса);
  2. `obligation_deadline` (наступление срока по обязательству);
  3. `notification_escalation_dispatch` (шаги эскалации просрочек);
  4. `sync_conflict` (уведомление `_notify_conflict_resolution` о разрешении коллизии).
* **Тихие часы:** Переиспользуется функция `outside_quiet_hours()` из `backend/app/notification_escalation.py`: если push не критичен, его отправка откладывается до окончания ночного интервала пользователя.
* **Лимиты:** Не более 1 пуша в 60 секунд по одной сущности (дедупликация в outbox по ключу `entity_type:entity_id`).

---

## 5. Тест-план
* **Unit-тесты:**
  * Генерация безопасного payload (валидация отсутствия конфиденциальных полей: сумм, цитат).
  * Корректная обработка ответов FCM (успех, retryable 5xx, удаление токена при `UNREGISTERED`).
  * Фильтрация тихих часов через `outside_quiet_hours`.
* **Контрактные тесты:**
  * Регистрация и отзыв токенов (`/api/notifications/devices/*`).
  * Проверка транзакционности `push_outbox` и идемпотентности отправки через `background_jobs`.
* **Ручная приёмка (критерий V6-04):**
  1. Реальный Android-телефон с установленным TWA-клиентом.
  2. Назначение задачи в веб-интерфейсе -> push приходит на телефон в течение 5 секунд.
  3. Нажатие на пуш -> открытие TWA ровно на карточке созданной задачи.
  4. Эмуляция недоступности FCM (подмена endpoint/mock 503) -> запись остаётся в `push_outbox`, при восстановлении сети досылается без потерь.

---

## 6. Порядок работ по PR

| PR | Задача | Оценка | Зона ответственности |
|---|---|---|---|
| **PR 1** | **Backend schema & models:** Таблицы `user_device_tokens (новое)`, `push_outbox (новое)`, миграция Alembic, эндпоинты регистрации токенов | 4–5 ч | Облачная сессия (код/тесты) |
| **PR 2** | **Backend FCM Dispatcher:** Сервис `PushChannelAdapter (новое)`, интеграция с `background_jobs`, readiness check секретов, удаление невалидных токенов | 6–8 ч | Облачная сессия + Оператор (проверка env) |
| **PR 3** | **Android App FCM Client:** Подключение `firebase-messaging`, `PuFirebaseMessagingService (новое)`, каналы нотификаций, передача deep link в TWA | 6–8 ч | Облачная сессия (код/сборка) |
| **PR 4** | **E2E & Delivery Pipeline:** Сборка релизного APK с подписью, прогон e2e сценариев доставки и ручная верификация V6-04 | 3–4 ч | Оператор на сервере (деплой, сборка APK с `PU_ANDROID_KEYSTORE`, приёмка на телефоне) |

---

## 7. Риски и открытые вопросы
1. **Энергосбережение вендоров Android (OEM Doze Mode):** Китайские прошивки (MIUI, HyperOS, ColorOS) могут глушить фоновый прием пушей без явного разрешения автозапуска пользователем.
2. **Синхронизация сессии в TWA:** токен передаётся через фрагмент URL при запуске (см. п.3); при смене пользователя на устройстве старый токен перепривязывается к новому пользователю тем же вызовом register. Если пользователь разлогинился в браузере, нативный Android-слой должен вовремя отозвать токен, чтобы push не пришел следующему пользователю устройства. Требуется триггер вызова `/revoke` при сбросе сессии в web-интерфейсе.
3. **Срок жизни `google-services.json`:** Файл содержит параметры проекта pu-workspace-57c66; при сборке APK он должен подтягиваться из защищенного пути CI/CD сервера без попадания в git-историю.