# UX-спецификация Communication Center

Статус: draft для реализации. Макет и продуктовая интеграция этим документом не
создаются. Функциональный источник требований —
[Mini-ТЗ почтового клиента](../MAIL_CLIENT_COMMUNICATION_CENTER_MINI_TZ_RU.md).

## 1. Компоновка

Desktop использует четыре области:

```text
+----------------+----------------------+----------------------+----------------+
| Ящик и папки   | Цепочки/сообщения   | Открытая цепочка     | AI / Контекст   |
| Gmail: user…   | поиск и фильтры     | письмо и composer    | проект/договор  |
| Входящие 12    | sender, subject     | From/To/Cc/time      | evidence        |
| Отправленные   | project/status      | body/attachments     | задачи/риски    |
+----------------+----------------------+----------------------+----------------+
```

На планшете панель AI/Контекст открывается drawer. На телефоне области становятся
последовательными экранами с видимой кнопкой «Назад к цепочкам»; draft сохраняется
до навигации. Фокус после закрытия drawer/dialog возвращается к вызвавшей кнопке.

В header всегда видны:

- выбранный ящик и provider;
- состояние подключения и последняя успешная синхронизация;
- активный scope: общий Inbox либо конкретный проект/договор;
- кнопка «Написать»;
- поиск с подписью фактической области.

## 2. Экран ящиков и папок

Переключатель показывает маскированный адрес, provider и состояние:

- «Подключён»;
- «Синхронизация…»;
- «Нет доступа — переподключить»;
- «Провайдер временно недоступен»;
- «Legacy-письма: требуется определить ящик».

Нажатие Gmail никогда не открывает Яндекс и наоборот. При неизвестном connection
нет fallback к первому подключению или активному проекту.

Папки: «Входящие», «Отправленные», «Черновики», «Архив», затем provider labels.
Счётчик непрочитанных не имитируется локально, если provider не дал актуальных
данных: показывается «—» и tooltip «Счётчик временно недоступен».

## 3. Список цепочек

Каждая строка содержит sender/participants, тему, короткий безопасный preview,
время, количество сообщений, attachment marker и badges:

- «Проект подтверждён» / «Укажите проект»;
- номер договора, если связь подтверждена;
- «AI-анализ готов» / «Требует проверки»;
- «Ожидает подтверждения ответа»;
- `UNKNOWN — проверить результат отправки`.

Фильтры применяются как явные chips и доступны с клавиатуры. Loading skeleton не
показывает искусственный процент. При pagination сохраняются selection, scroll и
exact mailbox cursor. Пустое состояние различает «писем нет», «фильтр ничего не
нашёл» и «нет доступа к ящику».

## 4. Цепочка и доказательство

Верх цепочки показывает источник и контекст отдельно:

```text
Источник: Gmail · Рабочий ящик 1 · версия проверена 14:32
Проект: предложен «Дубна 2027» (82%)  [Подтвердить/изменить]
Договор: не определён                    [Выбрать договор]
```

AI confidence подписан «уверенность модели», а не «точность» или «гарантия».
Исходный фрагмент/evidence открывается отдельной кнопкой. Для stale/unavailable/
revoked показывается безопасное состояние без preview скрытого фрагмента:

- «Источник изменился — анализ нужно обновить»;
- «Фрагмент недоступен с вашими правами»;
- «Доступ к ящику отозван»;
- «Не удалось подтвердить актуальность источника».

Подтверждение проекта/договора не подтверждает извлечённые сроки и не разрешает
отправку. Изменение контекста помечает зависящие AI-выводы как stale.

## 5. Composer

### 5.1. Режимы

Отдельные кнопки: «Ответить», «Ответить всем», «Переслать», «Написать письмо».
Composer явно показывает режим и поле From. Reply-all раскрывает To/Cc до ввода
текста. Bcc закрыт по умолчанию и открывается кнопкой.

Поля:

- From — только разрешённый exact mailbox;
- To/Cc/Bcc — tokens с inline validation;
- тема;
- rich/plain body с безопасным paste;
- вложения с именем, размером, source/version и состоянием проверки;
- подпись/шаблон с указанием версии;
- project/contract context и source thread.

Автосохранение показывает «Сохранено 14:35» либо «Не сохранено — повторить».
Закрытие с несохранёнными изменениями требует выбора: «Продолжить редактирование»,
«Сохранить черновик», «Удалить несохранённые изменения».

### 5.2. AI-помощник

Действия: «Кратко изложить», «Предложить ответ», «Изменить тон», «Проверить
обещания и сроки». Результат вставляется только после явного выбора. До вставки
показывается diff. Недоступный evidence не раскрывается в hover/tooltip.

Текст состояния при запрете внешнего AI:

> Политика проекта запрещает передавать содержимое внешней модели. Доступен
> локальный анализ либо ручное редактирование.

AI-предложение никогда не меняет From, To/Cc/Bcc, вложения, проект, договор или
режим отправки автоматически.

## 6. Проверка и подтверждение

Кнопка «Перейти к проверке» замораживает revision. Экран проверки содержит:

1. From и полный список To/Cc/Bcc;
2. тему и итоговый body;
3. вложения с exact versions;
4. project/contract и source thread;
5. AI/evidence warnings;
6. последствия: «Будет отправлено внешнее письмо из ящика …»;
7. номер revision и срок действия подтверждения.

Кнопки:

- «Вернуться к редактированию»;
- «Отклонить черновик»;
- `Подтвердить и отправить версию N`.

Нельзя использовать «Подтвердить всё». Confirmation dialog повторяет From,
количество получателей и вложений, но не скрывает Bcc. После изменения любого
поля отображается:

> Черновик изменён после проверки. Предыдущее подтверждение недействительно.
> Проверьте версию N+1.

## 7. Отправка и результат

После подтверждения карточка проходит фактические состояния:

- «Ожидает выполнения»;
- «Отправка начата — не нажимайте повторно»;
- «Отправлено» + время и ссылка на безопасный receipt;
- «Не отправлено — можно повторить»;
- «Результат неизвестен — проверяем у провайдера»;
- «Нужна ручная проверка результата».

Job progress показывается только при измеримом `processed/total`. Spinner не
означает отправку. Job `completed`, но outcome `UNKNOWN` отображается как
«Результат неизвестен», не как success.

При `UNKNOWN` главная кнопка — «Проверить результат», а не «Отправить ещё раз».
Blind retry отсутствует. После доказанного `NOT_APPLIED` кнопка называется
«Повторить отправку» и ещё раз проверяет approval/permissions/policy.

Повторный клик на исходную кнопку показывает существующий action, а не создаёт
новый. Reload восстанавливает exact draft/action/mailbox/context.

## 8. Ошибки и конфликты

| Ситуация | Текст | Действие |
|---|---|---|
| Draft 409 | «Черновик изменён в другой вкладке» | «Загрузить изменения и сравнить» |
| Source changed | «Исходное письмо изменилось или обновилось» | «Обновить цепочку» |
| Approval stale | «Подтверждение относится к прежней версии» | «Проверить новую версию» |
| Approval revoked/expired | «Подтверждение отозвано/истекло» | «Запросить подтверждение» |
| Connection rotated | «Подключение ящика изменилось» | «Проверить ящик заново» |
| Permission revoked | «Право отправки отозвано» | без retry |
| 422 recipient | «Проверьте выделенные адреса» | focus первого поля с ошибкой |
| Attachment unavailable | «Вложение недоступно или изменилось» | удалить/выбрать exact version |
| Provider unavailable before effect | «Сервис почты временно недоступен» | bounded retry/status |
| Outcome unknown | «Письмо могло быть отправлено» | reconciliation/manual check |
| Late response | визуально игнорируется для нового context | записать безопасную диагностику |

Ошибки содержат correlation ID для поддержки, но не body, адреса, filenames,
provider response или credentials.

## 9. Исправление после отправки

На sent message нет кнопки «Отменить отправку». Доступно:

- «Подготовить исправление»;
- новый corrective follow-up draft;
- явная ссылка «Исправляет письмо, отправленное …»;
- новая проверка, approval, action и receipt.

История исходного письма остаётся видимой. Corrective follow-up не меняет его
status и не обещает удалить письмо из ящика получателя.

## 10. Keyboard, screen reader и mobile acceptance

- последовательный tab order: mailbox → folders → threads → message → context;
- landmark и heading structure для четырёх областей;
- у icon-only controls есть accessible name;
- ошибки связаны с полями через `aria-describedby`, summary фокусируется;
- статус отправки объявляется `aria-live=polite`, критическая неизвестность —
  `role=alert` без повторяющегося спама;
- Escape закрывает dropdown/dialog, но не удаляет draft;
- горячие клавиши отправки не обходят review/approval dialog;
- touch targets не меньше 44×44 CSS px;
- на mobile адресаты, вложения и approval summary не скрываются горизонтальным
  скроллом;
- цвет не является единственным признаком состояния.

## 11. UX acceptance checklist

- [ ] Пользователь всегда видит, из какого ящика отправляется письмо.
- [ ] Общий Inbox не подменяется активным Persistent Project.
- [ ] Source identity и project/contract context визуально различимы.
- [ ] Reply-all до approval показывает каждого получателя и Bcc.
- [ ] AI output, extracted fact и human confirmation разделены.
- [ ] Изменение revision требует нового approval и показывает diff.
- [ ] `UNKNOWN` нельзя принять за «не отправлено» или повторить вслепую.
- [ ] После reload/late response сохраняется exact context.
- [ ] Sent message предлагает corrective follow-up, а не undo.
- [ ] Состояния revoked/stale/unavailable не раскрывают скрытый fragment.
- [ ] Все ключевые сценарии доступны с клавиатуры и на mobile.

## 12. Данные/действия контракта для UI

| Элемент | Требуемое поле/действие |
|---|---|
| Переключатель ящика | connection identity, mail connection, provider, generation, state |
| Папки/labels | mailbox/folder refs, capabilities, counters freshness, cursor |
| Строка цепочки | mailbox-scoped thread ref, participants projection, message count, context status |
| Source badge | SourceReference/SourceVersion, freshness, availability |
| Evidence drawer | locator, assessment, fragment ACL, safe unavailable reason |
| Project/contract chooser | ContextRelation hypothesis/version/confirmation |
| Composer | working draft version, operation, source message/thread pins |
| Recipients | normalized validated To/Cc/Bcc and own-address resolution |
| Вложение | attachment SourceReference/Version or staging ref, policy state |
| Review | immutable draft revision, canonical hash, effects, diff |
| Approval button | approval decision, authority/policy epochs, expiry |
| Send status | action/revision, business outcome, job status separately |
| Receipt | outcome observation, attempt, safe provider/time projection |
| Retry | only `NOT_APPLIED`, fresh checks and new attempt |
| Correction | relation to irreversible source action and new revision |

Отсутствующие поля не имитируются на клиенте. Если backend не возвращает
freshness, capability, exact revision или business outcome, UI показывает
«недоступно» и оформляется integration request.
