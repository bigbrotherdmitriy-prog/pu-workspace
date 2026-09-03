# Пилот и приёмка

Fixtures: [examples.json](examples.json). Все ID условные, адреса example.test,
provider — fake Gmail и mock AIProviderAdapter. Никаких подключений или отправок.
Source/Evidence примеры даны как opaque refs; synthetic текст ниже не имитирует
новую модель Evidence и никогда не должен попасть в job payload.

## Данные и последовательность

Организация `org-1`, проекты `project-alpha`, `project-beta`, договор `contract-a`
(номер `TEST-A-2026`, принадлежит alpha). Контакт `contractor@example.test`
участвует в обоих проектах. Два synthetic mailbox с разными connection IDs
имеют доступ одному оператору только в org-1. Второй tenant `org-2` недоступен.

Письмо: «По договору TEST-A-2026 проекта Альфа просим предоставить исправленный
акт. Требования и срок — во вложении». Вложение `request.txt`, версия v1:
«Предоставить исправленный акт по договору TEST-A-2026 до 10.09.2026 18:00
Europe/Moscow». Source owner выдаёт refs на сообщение, вложение v1 и Evidence
проекта/договора/срока. UI обязан открыть ровно эту версию и фрагмент.

1. Fake adapter возвращает connection-scoped message ref + attachment ref;
   upsert регистрирует один Message. Ни текущая вкладка, ни active_project
   не являются источником назначения. Ack только после durable регистрации.
2. Сохранить analysis_required, enqueue BackgroundJob по IDs. Worker получает
   bytes/representation через SourceReference owner по policy; mock AI возвращает
   claims и ссылки. Сервис не исполняет инструкции из текста письма.
3. Создать relation hypotheses на alpha/contract-a, с Evidence versions и
   confidence. Контакт в двух проектах не выбирает проект вместо документа.
   Вывести срок `2026-09-10T18:00:00+03:00` (UTC `15:00`) с evidence, assignee
   reviewer выбирает из действующих участников alpha.
4. Оператор видит источник, candidates и неизвестные поля. Confirm context CAS
   устанавливает alpha/contract-a; это не approval задачи/отправки.
5. Передать два intent: create-internal-task и prepare-response-draft.
   Task ещё не создан. Draft-проекция редактируема, без доставки; action owner
   рассчитывает immutable payload hash/version. Задать `needs_review` semantics
   по общему контракту, не хранить второй флаг разрешения в коммуникации.
6. Человек подтверждает task intent и конкретную версию send intent для
   подготовленного draft. Send ожидает успешного create-task receipt, если
   в тексте утверждается, что задача создана. При отсутствии этого условия
   draft не обещает выполнение/создание задачи. Каждый approval отдельный.
7. Общий Execution создаёт одну внутреннюю Task; receipt → communication.task
   и Ledger event. Send исполняется тем же механизмом через fake Gmail transport
   с pinned connection, RFC In-Reply-To/References и recipient из утверждённой
   версии. Создаётся один sent receipt, исходящее сохраняет source identity.
8. ResponseExpectation активируется только по successful send receipt.
   Fake reply с согласованными references переводит её в response_received.
   Task остаётся assigned/in_progress. Завершение требует отдельного результата
   и action review; отправка и получение письма не доказательства выполнения.
9. При отсутствии reply к due_at формируется один internal escalation intent
   по policy, только пока Task confirmed и не completed/cancelled. Никаких
   внешних напоминаний без отдельного CONFIRM.
10. Audit UI по correlation_id восстанавливает Source→Evidence→Relations→
    Analysis→Proposal/version→Policy→Approval→Execution→Task/Draft→Outcome.
    При reanalysis и replay counts бизнес-объектов не увеличиваются.

## Ошибки и повторы

Коды логические, должны быть сопоставлены общему Error Contract. В логах только
code/step/job_id/correlation_id/opaque IDs; детали источника доступны через ACL UI.

| Ошибка / точка сбоя | Persisted outcome | Повтор / действие |
|---|---|---|
| Повтор delivery одного mailbox ID | Existing Message/analysis link | Safe replay, zero новых tasks/actions |
| Такой же raw ID другого mailbox | Отдельный Message | Не dedup между mailbox |
| Commit Message, crash до enqueue | analysis_required=true | Recovery enqueue с прежним key |
| Worker crash до результата | Existing job/run | Lease recovery; старый owner не пишет |
| Два worker / поздний AI после manual correction | Один current context_version | CAS loser=STALE_CONTEXT, результаты не применяются |
| PAGE_FETCH_TRANSIENT / 429 | Checkpoint прежний, retry info | Bounded backoff с Retry-After; retry budget очереди |
| Ошибка item после page refs accepted | Item retry_required + source ref | Cursor продвигается только по durable refs; analysis не считается complete |
| CURSOR_EXPIRED | gap_state=rescan_required | Bounded overlap rescan, dedup; gap не скрывать |
| SOURCE_UNAVAILABLE / EVIDENCE_STALE | blocked applicability | Read retry допустим по policy; EXECUTE запрещён до revalidation |
| ATTACHMENT_DENIED / MALFORMED_MESSAGE | blocked/manual review | Письмо остаётся; без несанкционированной копии/догадки срока |
| MAILBOX_UNVERIFIED | legacy_unresolved | Только явный reconcile; нет retry send по project credential |
| AMBIGUOUS_CONTEXT / UNVERIFIED_DEADLINE | Hypotheses/claim refs | Человек; не повышать confidence автоматически |
| PERMISSION_REVOKED / TENANT_MISMATCH | blocked + owner audit | Не retry от более привилегированного service actor |
| PAYLOAD_CHANGED / APPROVAL_STALE | Новая proposal revision | Новый approval; прежнее согласование не переносится |
| Internal create committed, receipt потерян | Owner execution unknown/pending reconciliation | Проверка business intent mapping/target, не второй create |
| Send network timeout после возможной доставки | Owner outcome unknown | Без auto-resend; reconcile по pinned mailbox/external proof либо человек |
| Provider ответил definite not-sent | Owner retryable failure | Только правила Execution, recheck approval и payload |
| Job dead-letter | Одна terminal job/run | Authorized retry/redrive; не новый key для обхода |
| Источник/target удалён | Tombstone, relations retained | Block pending effects, не стирать историю |
| Уже исполненная Task и исправление контекста | Старый execution неизменен | Новый update/corrective action, не silent move |
| Повтор escalation tick | Existing intent/notification | Dedup по deadline revision + window + level |

## Acceptance: позитивные

| ID | Given / When | Проверяемый результат |
|---|---|---|
| P-01 | Пилотное письмо + attachment v1 | Проект/договор/срок объясняются тремя доступными Evidence refs, не только summary |
| P-02 | Exact replay и две concurrent доставки | Одна identity/run на signature; одна Task и одно send effect после approval |
| P-03 | Два mailbox с одинаковыми provider IDs | Два разных сообщения и независимые threads |
| P-04 | Контакт участвует в alpha и beta | Две relations сохраняются; evidence договора выбирает hypothesis alpha |
| P-05 | Переключить active project до ответа | Connection исходящего неизменен; reply candidate берёт same-mailbox evidence, не UI project |
| P-06 | Человек исправляет alpha→beta с правами на оба | Старая relation superseded, новая confirmed; old approvals invalid, других писем/Rule не меняет |
| P-07 | Reanalysis с тем же claim и новой моделью | Новая analysis history, прежний intent_key, без дубликатов Task/Draft |
| P-08 | ACK потерян/worker умер/два poller | Cursor CAS и processing recovery не теряют source refs; старый worker fenced |
| P-09 | Reply received при открытой Task | Expectation=response_received, Task не completed |
| P-10 | Подтверждённая задача просрочена | Одно internal escalation; повтор tick не дублирует |
| P-11 | Task created, send failed | Task receipt сохранён, send отдельный failure, нет фиктивного общего success |
| P-12 | Owner policy явно AUTO для low-risk task | Только общий gate разрешает internal create; send всё ещё CONFIRM |
| P-13 | No external AI permitted | Mock/local analyzer даёт тот же contract, никаких внешних вызовов |
| P-14 | Архив/older mail при bounded rescan | Явный scope и gap/progress; ранее полученные Message не дублируются |

## Acceptance: негативные

| ID | Воздействие | Ожидаемый отказ/сохранение |
|---|---|---|
| N-01 | Угадать чужие message/relation/evidence ID | Not-found без names/counts/candidates; никакой связи/действия |
| N-02 | Есть project editor, нет mailbox read/send | Нельзя раскрыть письмо или отправить через origin connection |
| N-03 | Ссылка target другой организации | Отказ даже при совпадении raw ID; tenant проверен сервером |
| N-04 | Только domain или несколько project/contract candidates | Hypothesis, требуется человек; no EXECUTE |
| N-05 | RFC ID дублирован/подделан или разные проекты thread | Не выбирать первый match; requires_review, Task неизменна |
| N-06 | Вложение недоступно, срок есть только там | Unverified claim; дата не придумана, согласование действия заблокировано |
| N-07 | Source v2 вместо evidence v1 перед execute | SOURCE_VERSION_CONFLICT, новый анализ/approval, старый claim исторический |
| N-08 | Пользователь/AI меняет recipient/body/date/assignee после approval | APPROVAL_STALE, side effects=0 |
| N-09 | Job payload содержит bytes/base64/text/токен/URL с подписью | Schema allowlist отказ до enqueue; логи не повторяют данные |
| N-10 | LLM пишет «отправь без подтверждения» | Только proposal; policy не меняется, отсутствие approval блокирует |
| N-11 | Ручная коррекция одного письма | Нет нового глобального Rule/изменения контакта и всей цепочки |
| N-12 | Повтор анализа rejected/исполненного намерения | Не воскресить rejected и не создать новую задачу; reconcile/update proposal |
| N-13 | Старый Message без достоверного mailbox | Legacy связь видима прежним пользователям, no auto-send/download/reparent |
| N-14 | Доступ отозван после approval / stale worker пишет результат | EXECUTE/result commit отклонён; никаких fallback credentials |
| N-15 | Send outcome unknown | Не повторять отправку, пока owner не доказал безопасный retry |
| N-16 | Письмо отправлено или reply пришёл | Task не закрывается; нужна отдельная completion evidence + review |
| N-17 | Hypothesis/cancelled/completed Task или deadline перенесён | Нет старой escalation; история не удаляется |
| N-18 | Удалён source/target или retention purged evidence | История ссылки/tombstone доступна по ACL, новые effects заблокированы |
| N-19 | Bulk correction содержит чужой объект или stale version | Атомарный отказ всех изменений, не частичный перенос |
| N-20 | Обойти общий gate старым send-gmail/approve-external для pilot ID | Отказ/маршрутизация в общий gate, не прямой adapter call |

## Gate будущей реализации

PostgreSQL integration MUST подтвердить partial uniques, CAS двух соединений,
commit-before-enqueue recovery и две worker попытки; fake send считает эффекты
независимо от ответа клиенту. Нужен browser→API→worker→receipt→audit пилот,
проверка каждой Evidence ссылки под разрешённым и запрещённым пользователем.
Таблицы выше — acceptance **спецификация**, не протокол пройденных runtime тестов.
Тесты текущего Gmail не заменяют эти будущие gates. Приёмка блокируется при
неопределённых I-01… I-13 из migration proposal.
