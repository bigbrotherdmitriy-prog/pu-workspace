# UX-спецификация CONFIRM-пилота

Статус: **внутренний UX draft**, не утверждённый API и не расширение product scope.
BASE_SHA: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.

## Основания и границы

Прочитаны foundation report и integration README/glossary/ownership/decisions/
acceptance/migration-handoff/pilot.json/validator. Supplemental commits A/B/C
прочитаны через git show, без cherry-pick:

- Source/Evidence: 7674e973401301d4d31e8561ce7875427a600869.
- Context: 7edea2b5e6b362b856dfb752ee4a09ae598e12d2.
- Trust/Claim: f384ae533d6ac48229d2bf00aa2659b8b3895ca6.

DOCX `PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx` прочитан
как требования, не полномочия на внешние действия. Использованы §17 «Интерфейс»,
Strategic Architecture Requirements §§1–6 и Strategic Trust & Enterprise
Intelligence §§1–9: evidence-backed proposal, отдельные decisions, exact payload
approval, visible audit, federated source, новый action для compensation.
Более широкий пример отправки/AUTO в DOCX **не включён**: явное задание и
integration ADR-07/08 ограничивают пилот CONFIRM internal create/cancel.
Старое «только первый срез» не означает удаление Gmail/финансов/OCR.

Визуальная основа read-only: frontend/src/brand.css, styles.css, модули
Proposals и ProjectLaunchWizard. Переиспользован язык, не код компонентов:
graphite #111714, green #087a59, canvas #f1f4f2, белые cards,
border #dbe3df, radius 12px, системный шрифт, короткие labels.
Контраст secondary текста повышен до #52655b; нет внешних fonts/icons.

## Карта экранов

```text
Входящее M6 (источник и origin, ещё не подтверждённый контекст)
  ├─ Evidence E16/r1 → конкретный source observation V15/r1
  │    └─ отдельная проверка evidence assessment
  ├─ 1. Project + Contract candidates → подтверждение контекста
  ├─ 2. DeadlineClaim C17/r1 → отдельная проверка срока
  └─ 3. Exact Action revision → диалог последствий → approval
       └─ ожидание → выполнение → Task + APPLIED receipt + audit
            └─ отдельный cancel proposal → approval → cancel receipt
```

Это одна рабочая страница, один native dialog и производная секция результата.
Desktop: слева письмо/evidence, справа три решения. Mobile: сначала источник,
затем решения, затем результат. Пульт ошибок отделён от продуктового сценария.
Нет кнопки «Подтвердить всё», AUTO toggle или отправки письма.

### Входящее и контекст

Показывать sender/subject только после разрешённого metadata read, рядом
provider/account/namespace (без tokens), Message/source IDs в details, attachment.
Origin project из source — явно «не подтверждённое назначение».
Active project в навигации **никогда** не является контекстом входящего.
При неизвестной identity — «Источник не сопоставлен; требуется проверка подключения»,
без догадки по email/названию/active project.

Кандидаты обозначаются «AI-гипотеза». Project и принадлежащий ему Contract
подтверждаются атомарно одной ContextConfirmation, но не вместе с claim/approval.
Отсутствует подходящий кандидат → не продолжать: correction flow IR-03.
Смена проектного просмотра скрывает чужую карточку, не меняет её связь.
Макет показывает только одну согласованную пару Альфа/ГК-01, не фиктивный picker.

### Evidence

Показывать отдельно: источник, exact observation, evidence pin/locator,
разрешённый фрагмент, извлечённый факт, вывод AI, extractor/model version,
confidence_kind и confidence. Число уверенности — не вероятность юридической
истины; без calibration неизвестное значение обозначается «не оценено».
Mock 0,92 — вымышленное значение, явно подписано fixture.

Assessment verified не подтверждает DeadlineClaim. В макете для него отдельная
кнопка «Проверить доказательство»; она не прячется в решении о задаче.
Реальный Source facade A пока разрешает только metadata и запрещает fragment.
Потому цитата, страница 1 / пункт 2 — **UX fixture / IR-02**, не существующий reader.
В реализации без этого контракта показывать недоступное доказательство,
а не обходить deny через legacy preview/OCR/cache.

| Производный статус UI | Условие / поведение |
|---|---|
| Актуален | Exact version/current + fresh + available + известные ACL/policies/TTL |
| Не проверено | Assessment unverified; запрет последующего approval gate |
| Устарел | Freshness stale / истёк TTL; новая проверка, не подмена на latest |
| Источник изменился | Changed SourceVersion; прежний pin сохраняется, новый proposal/review |
| Недоступен | Provider unavailable/deleted; без фрагмента, нет автоматического retry mutation |
| Доступ отозван | Authorized reason от server; иначе нейтральное «Не удалось проверить» |
| Не удалось проверить | Unknown ACL/version/policy/TTL; fail closed |

Не показывать запрещённую цитату в DOM, title, tooltip, aria-label, поиске,
notification или audit. Проверка свежести той же версии не переписывает evidence.
Браузерный макет убирает quote из DOM, но fixture текст остаётся в исходном JS:
это допустимо только для вымышленных данных, не шаблон защиты реальных документов.

### DeadlineClaim

Отдельно показывать дату, timezone, date-only precision, pin, evidence set,
verification и reviewer. «Срок верный — подтвердить» review-ит exact claim.
Дата 10.09.2026 не превращается в 00:00 или 18:00. Если источник содержит
время, которое целевой Task не поддерживает — блокировать, IR-04, не обрезать.
Ошибка даты → correction с новым claim revision и provenance;
никакого незаметного редактирования в approval dialog.

### Approval

Диалог содержит title, assignee, deadline/precision/timezone, project/contract,
source/evidence/claim pins, exact action revision, consequences и способы отмены.
Реальная реализация получает server-rendered seal / hash, capability и expiry,
а не рассчитывает полномочия из UI. Hash в details, но все значимые поля видны
обычным текстом; label «rN» сам по себе не защита.

Изменение title/assignee/payload/claim/context/evidence/policy/account требует
нового freeze и нового approval. Старый снимок нельзя принять для новой версии.
Только freshness-only при доказанной той же версии и непросроченном grant
может пройти повторный server gate без нового seal.
В макете fingerprint — JSON snapshot для локального сравнения, **не SHA-256 seal**.

Default focus диалога — «Вернуться без разрешения»; Enter не одобряет скрытую
форму. Escape закрывает без решения, Tab остаётся в modal, focus возвращается
к инициатору. Revoke approval не означает отмену уже применённого action.

## Состояния выполнения

| Текст пользователю | Основание | Разрешённое следующее действие |
|---|---|---|
| Ожидает подтверждения | Не хватает context/claim/review/grant | Завершить отдельную проверку |
| Ожидает выполнения | Valid grant + pending dispatch; job может ещё отсутствовать | Смотреть состояние; revoke до исполнения |
| Выполняется | Server execution/reservation, не только queued job | Ждать / читать состояние, без повторного create |
| Создана задача | APPLIED receipt с exact action/approval + подтверждённая Task | Открыть результат/историю; отдельная отмена |
| Ошибка без эффекта | Авторитетное доказательство rollback/NOT_APPLIED | Только разрешённый redrive после повторных guards |
| Результат неизвестен | Нет авторитетного outcome; timeout/расхождение projection | Сверка результата, запрещён blind retry |
| Отмена ожидает разрешения/выполнения | Новый cancel action/grant/intent | Независимый live gate |
| Задача отменена | Cancel APPLIED receipt и новая Task version | Читать оба результата и историю |

В DB-only C facade реальные failures откатываются без NOT_APPLIED receipt;
UNKNOWN/NOT_APPLIED receipts там не создаются. Эти UX-состояния — IR-07 для
read projection / safe reason codes, не выдуманные HTTP enum/endpoints.
Job completed без receipt **не** даёт «Создана задача».
Progress отсутствует: нет фиктивных процентов/таймера ожидания. Если backend
предоставит измеряемые done/total/phase, показывать отдельно transport progress.

После reload UI не POST-ит повторно: читает action/receipt по прежнему server key,
проверяет ACL/current scope; до ответа — «Проверяем состояние».
Макет сохраняет только synthetic sessionStorage, поэтому это симуляция reload,
не proof server recovery. Поздний ответ не переключает выбранный проект.
Response-binding: tenant + actor + project + message + action/pins + request epoch.

## Отмена

«Запросить отмену задачи…» открывает отдельный cancel dialog с Task target/version,
expected assigned/internal-only, исходным create receipt и новыми последствиями.
Только после нового approval применяется cancel. Task остаётся, меняется status,
создаётся новая history/receipt; create receipt остаётся APPLIED.
На changed Task/внешние зависимости/отсутствие capability — отказ, IR-08.
Не обещать undo письма или удаления документа. Реальной отправки в пилоте нет.

## Ошибки и русские тексты

| Событие | Текст и восстановление |
|---|---|
| 409 | «Данные изменились. Сравните новую версию и подтвердите заново. Автоповтора нет.» |
| Approval revoked | «Разрешение отозвано. Оно больше не разрешает новое выполнение.» Уже созданную Task не скрывать |
| Approval expired | «Срок разрешения истёк. Проверьте текущую версию и выдайте новое разрешение.» |
| Changed source | «Источник изменился. Прежнее доказательство относится к другой версии.» Новый review/seal, не latest fallback |
| Revoked rights | «Доступ не подтверждён. Выполнение заблокировано.» Уточнение причины только если server разрешил |
| Повторный клик | «Запрос уже принят. Проверяем его результат.» Тот же command key, не новый action |
| Reload running | «Проверяем состояние ранее принятого действия…» Без повторной mutation |
| Late response | «Ответ относится к прежнему просмотру и здесь не применён.» Без автоперехода |
| Unknown | «Результат неизвестен. Не запускайте действие повторно; требуется сверка.» |
| No fragment | «Фрагмент недоступен: актуальность или доступ не подтверждены.» Не раскрывать через подсказку |
| Unknown roles/policies | «Действие недоступно: правила подтверждения не определены.» Не предлагать admin bypass |

Все mutation-кнопки блокируются на in-flight; обязательный server idempotency
остаётся независимо от disable. Error feedback live region, не исчезающий toast.
Серверный reason code переводится по allowlist; raw exception/SQL/body не выводить.

## UX acceptance checklist

- [x] Есть явная demo-пометка и отсутствие реальной отправки/AUTO.
- [x] Узнаваемые graphite/green, desktop/mobile без горизонтального overflow.
- [x] Evidence, context, claim, action approval не объединены одной кнопкой.
- [x] Visible source/observation/fixture locator; факт отличается от AI-вывода.
- [x] Stale/unavailable/revoked/unknown блокируют новый dispatch; quote удалён из DOM.
- [x] Exact version snapshot; старое локальное подтверждение отвергается после edit.
- [x] Created требует отдельного результата; completed job + unknown не становится success.
- [x] Cancel отдельный; исходный Task/receipt/history не исчезают.
- [x] Duplicate click, 409, revoke, expiry, reload и late project response покрыты.
- [x] Native keyboard controls, labels, visible focus, Escape/Tab/modal focus возврат.
- [x] Chromium visual inspection desktop/mobile и сценарии — только mock UX.
- [ ] Production UI → authenticated APIs → queue → Task/receipt, PG crash/race gates.
- [ ] Authorized fragment reader / real reason codes / approved human roles.
- [ ] Screen reader и пользовательское usability исследование.
- [ ] Поддержка Safari/Firefox, mobile screen reader, forced colors.

Чекбоксы PASS относятся только к этому макету и его tests. Они не закрывают
runtime INT-01…23 и не подменяют приёмку интегратора.
