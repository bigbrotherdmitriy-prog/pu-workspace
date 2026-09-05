# UX-пилот: письмо → проверенная внутренняя задача

**Демонстрационный макет, данные вымышлены.** Не часть приложения,
не серверная авторизация и не результат интеграционного тестирования A/B/C.

Откройте [index.html](index.html) в современном браузере. Работает без сервера,
CDN, установки зависимостей и сетевых запросов. Если браузер запрещает
sessionStorage для file URL, появится предупреждение: reload начнёт demo заново.

## Что попробовать

1. «Проверить доказательство».
2. «Подтвердить проект и договор» — только контекст.
3. «Срок верный — подтвердить» — отдельно DeadlineClaim.
4. «Проверить и разрешить…» — прочитать точную версию и последствия.
5. В **пульте сценариев**: «Начать выполнение», затем «APPLIED + Task + receipt».
   Пульт — средство симуляции, не рекомендуемый production UI.
6. «Запросить отмену задачи…» → отдельное разрешение → в пульте завершить отмену.
   Первое создание и receipt сохраняются.

Ошибки: после нового старта demo используйте варианты source state, 409, revoke,
expiry и «Job completed, результат неизвестен». Последний доступен после начала
выполнения; повторное исполнение заблокировано.
Для позднего ответа: проверить evidence → «Удержать ответ контекста» →
перейти в Persistent Project → «Доставить задержанный ответ».
Для reload: перезагрузить во время running; состояние этой вкладки сохранится,
новая mutation не запускается. Реальное приложение обязано перечитать сервер.

## Материалы

- [Спецификация, карта экранов/состояний, тексты и acceptance](spec.md).
- [Элемент UI → контракт и запросы интегратору](contract-map.md).
- [Итоговый аудит](../../audits/v54-pilot-ux-spec.md).
- index.html / style.css / app.js / state.js — автономный макет.
- state.test.cjs — тесты локальной модели; не backend tests.
- browser-check.cjs — optional QA с уже установленным Playwright.
- desktop.png / mobile.png / approval-mobile.png — только synthetic screenshots.
- browser-result.json — выполненные browser checks.

## Проверки без новых зависимостей

Из этой папки:

```text
node --test state.test.cjs
node --check app.js
node --check state.js
```

Для optional browser QA задайте `PU_UX_PLAYWRIGHT` абсолютным путём к уже
установленному модулю `@playwright/test` и выполните `node browser-check.cjs`.
Ничего не устанавливать в эту worktree. Harness читает только локальные assets,
блокирует неожиданные запросы, записывает screenshot/result только в эту папку.
Node/PW нужны лишь QA; конечному HTML они не нужны.

Ограничения макета: фиксированные synthetic project/contract/claim, нет настоящего
seal, server time, ACL, API, queue, source bytes или Task. Изменение title/assignee
создаёт локальную версию, не backend ActionRevision. Мария/Иван — fixture labels,
не политика ролей клиента. SessionStorage только для demo, не проект auth/storage.
