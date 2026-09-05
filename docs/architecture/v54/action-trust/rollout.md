# Совместимое внедрение и вопросы интегратора

PROPOSED. Ни одна стадия ниже этим commit не реализована и не разрешена к deploy.

## Этапы и stop gates

| Этап | Совместимое изменение | Условие перехода |
|---|---|---|
| 0. Согласовать контракт | Зафиксировать владельцев authority/policy/evidence APIs, ADR MVP5/MVP6 и формат hash | Решения владельца, integration review, известные ограничения провайдеров |
| 1. Shadow descriptors | Обёртки читают existing domain state и вычисляют hypothetical decision без нового исполнения | Нет записей «человек approved» из legacy status; нет отправок или изменения прав |
| 2. Общая trust envelope | Добавить revision/binding, ledger extension к AuditLog, action reservation и pending-dispatch через имеющуюся очередь | Миграции отдельным integration потоком; проверка tenant/unique/append constraints; регрессии зелёные |
| 3. Только internal task CONFIRM | Один DB-only helper Task/TaskHistory вызывается старым входом и фасадом; exact effect set без Obligation/publish | N03, N06–N19, N28–N29, N35–N39 проходят, атомарность проверена PostgreSQL |
| 4. Узкий AUTO pilot | После решения владельца включается versioned policy только task.internal.create с scope/expiry/quota | N01–N05, N09–N14, N34; default disabled, explicit audited enable; иначе AUTO acceptance остаётся открытой |
| 5. External send CONFIRM | Тот же Gmail/provider adapter, immutable approved message, reservation/UNKNOWN/reconcile | N07, N15–N27, N30, N36–N38, N41; при недоказанном reconcile автоматический resend запрещён |
| 6. Остальные existing flows | По одному домену: Organizer operations, затем финансовые решения; существующие guards сохраняются | Guards rollback/version/evidence; отдельные finance approvals; domain regression suite |

Task pilot не требует нового scheduler, provider или AI agent. Production feature
flags и обработка незавершённых операций согласуются отдельно; не включать новую
policy автоматически при обновлении версии приложения.

## Отображение и cutover

- Stable domain binding: organization + domain type/id + action kind + intent
  generation. Для ещё не созданной Task — source request identity + generation,
  не название задачи. Повтор команды из другого канала должен разрешаться в тот же
  intent. Способ выдачи generation серверный; новый ключ не обходит UNKNOWN.
- Existing OrganizerProposal — пакет, OrganizerAction — элемент; OrganizerOperation
  остаётся execution projection. Ledger получает immutable события через audit
  facade; `reconcile_operation` может обновлять projection, но не старый ledger event.
- ResponseDraft.id/status/sent_external_id сохраняются. Новая revision привязывается
  к draft version; существующий sent ID — legacy receipt, не выдуманный approval.
- Existing Task/TaskHistory/CashFlowEntry остаются authoritative domain state.
  Новая trust запись не копирует задачи, финансы, contacts или full document content.
- Миграция legacy `approved` не создаёт задним числом GRANTED человеком. Уже
  завершённые действия отмечаются `legacy_unbound` с имеющимися receipts. Для
  неисполненных — новый freeze и настоящий approval; ambiguous send — reconciliation.
- Переключение по action type/scope под версией cutover: один маршрут исполнения.
  Все web/API/Telegram/automation entrypoints этого типа вызывают один gate. Нельзя
  оставить публичный legacy execute как обход либо отправлять одновременно двумя
  путями. До cutover не заявлять, что весь продукт соответствует v5.4.
- Старые pending jobs и провайдерские UNKNOWN инвентаризируются до переключения.
  Action mapping/reservation фиксируется прежде replay; lease release не является
  разрешением повторить внешнюю отправку. Drain должен сохранять очередь, не удалять её.
- Rollback feature flag запрещает новые действия нового формата, но не удаляет
  ledger/intent и не возвращает UNKNOWN в legacy retry. Reconciliation продолжает
  работать. Автоматическое снижение требований до старого approved запрещено.

## Границы транзакций и ownership

`queue.enqueue` и несколько domain helpers сейчас коммитят внутри себя. До
реализации нужно согласовать transaction owner: внутренний эффект/receipt/ledger
атомарны, dispatch intent переживает отказ между commit и enqueue. Использовать
существующий BackgroundJob публичный контракт; pending-dispatch хранится при
action projection и повторно enqueue с тем же ключом, не отдельной очередью.

Планируемые изменения модели/DB permissions из [контракта](contract.md) — только
проект; SQL/Alembic в пакете нет. SourceReference/Evidence и ContextRelation не
мигрируются этим потоком. Их missing/unavailable ответы блокируют execution,
а не запускают локальную альтернативную модель хранения доказательств.

Технические логи: allowlist job_id/action_id/attempt_id/correlation/safe_code,
без содержимого и полного provider exception. Ledger ACL проверяет tenant/project;
сведения о получателях/финансовых значениях доступны через защищённые domain refs.
Нужен отдельный retention/redaction договор с владельцем данных, а не обещание
неизменяемого вечного хранения персональных данных.

## Вопросы интегратора / решения владельца

| № | Кто | Решение / интерфейс, нужный до реализации |
|---|---|---|
| Q01 | Владелец продукта | Утвердить или отклонить narrow AUTO MVP5 ADR; до ответа только CONFIRM, AUTO-сценарий не закрыт |
| Q02 | Auth/организации | Какой verified tenant ID и role/authority epoch использовать; кто включает AUTO и кто approve? Глобальный admin не назначается по умолчанию |
| Q03 | Владелец продукта/security | Self-approval для LOW Task допустим? Для финансов/внешних писем нужна ли separation of duties? До решения не давать service principal право human approve |
| Q04 | Evidence поток | Resolve(ref id/version, actor, scope) → access/integrity/freshness/verified; TTL и unavailable semantics; как атомарно наблюдать отзыв доступа? |
| Q05 | ContextRelation поток | Формат typed id/version и target binding; какие изменения relation инвалидируют proposal? В пилоте консервативно любая смена bound version |
| Q06 | Queue/DB интегратор | Общая транзакция internal effect/receipt/ledger; pending-dispatch и существующий enqueue; worker reconciliation privilege без права approve |
| Q07 | Audit/DB интегратор | Расширение AuditLog versus extension table; append writer role, sequence uniqueness, retention events, projection rebuild, tenant ACL |
| Q08 | API/frontend интегратор | Где human видит sealed target/effects/evidence/expiry, подтверждает exact hash и отдельно отзывает; как UI показывает UNKNOWN и late revoke? |
| Q09 | Domain Tasks | DB-only create/cancel helper, отсутствие Obligation/внешней публикации, Task version/CAS; границы assigned-only cancellation |
| Q10 | Provider/Gmail поток | Поддержка key/conditional write/receipt lookup и доказуемого NOT_APPLIED; Message-ID/thread не объявлять гарантией exactly-once |
| Q11 | Policy владелец | Точные AUTO quota/expiry, risk catalog, evidence verification требования и permitted service mandate; fixture 5/час не утверждённый лимит |
| Q12 | Контракт API | Принять pu-action-c14n-v1 и versioning, limits integer/Unicode/JSON; единый валидатор envelope и контракт immutable payload refs |
| Q13 | Finance владелец | Какие действия создают обязательство, кто утверждает, какие значения payment fact обязательны; human attestation без банковской выписки |
| Q14 | Operations/security | Resolution UNKNOWN, запрет replay без доказательства, race revocation/dispatch, ручной incident workflow; нельзя обещать revoke после провайдерского эффекта |
| Q15 | Владелец ТЗ | Подтвердить редакционную иерархию: файл v5.4 содержит титул v5.1 и более поздние Strategic Trust appendices |

Порядок решения: Q01–Q07, Q09, Q11–Q12 блокируют policy-bound task pilot;
Q08 блокирует пользовательскую приёмку CONFIRM/revoke; Q10/Q14 блокируют заявление
о безопасном автоматическом восстановлении external send; Q13 — finance cutover.
Отсутствующий API другого потока — явный blocker, не основание дублировать его модели.
