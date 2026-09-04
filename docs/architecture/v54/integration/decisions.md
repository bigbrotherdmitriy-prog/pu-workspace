# ADR: карта пересечений и решения

`ALIGNED PROPOSAL` означает согласованность документации, не утверждение owner
policy и не реализацию. `OWNER DECISION REQUIRED` требует явного решения.

| ADR | Проблема / варианты | Согласованное предложение | Последствия / статус |
|---|---|---|---|
| 01 ID | Source integer, Context UUID/opaque strings, Action mixed; варианты глобальный UUID или boundary refs | Единственный ObjectRef/VersionPin в glossary, existing PK сохраняются | Новый wire version, старые примеры не API requests; ALIGNED PROPOSAL |
| 02 Версии | Evidence record_version двусмысленен, Action «любая version» инвалидирует | Immutable evidence/source pins отдельно assessment; approval semantic binding, live recheck | last_checked_at не reapproval; bytes/claim/policy change требует freeze; ALIGNED PROPOSAL |
| 03 Claim | Context делегирует claim Evidence, у Evidence только фрагменты; варианты universal Claim engine / Context claim / Task domain | Узкий DeadlineClaim Task facade, стабильный anchor, evidence[] | Никаких Task до approval, review != context confirm; ALIGNED PROPOSAL |
| 04 Identity | MailConnection свой account master против Source identity; варианты два registry / общий | Integration ConnectionIdentity, MailConnection namespace extension | Credential refresh не меняет account, legacy unresolved; ALIGNED PROPOSAL |
| 05 Transaction | enqueue/helpers commit; варианты новая очередь / pretend outer transaction / durable intent | Existing queue + pending_dispatch, Trust owns T2, DB-only domain helper | Нужна отдельная минимальная реализация helper+receipt+audit txn; ALIGNED PROPOSAL |
| 06 Audit | Context нужен audit до action; append-only против retention | Один writer AuditLog extension, typed subject streams, retention-controlled purge | Без второго ContextLedger, no infinite PII; ALIGNED PROPOSAL; сроки OWNER DECISION REQUIRED |
| 07 Scope | Context pilot включает fake send/reply/escalation, Action AUTO примеры | Первый исполняемый срез только task create/cancel CONFIRM и draft | Send/reply/escalation future-only, UNKNOWN fake test; ALIGNED по явному заданию пользователя |
| 09 Deadline | Context timestamp 18:00, Task date-only | Пилотный fixture date-only с timezone; время из источника не терять и не silently truncate | Для time-specific claim future target support либо BLOCKED, не обещать time scheduling; ALIGNED PROPOSAL |
| 10 Trust | Context/payment/approval conflation; admin и privacy как authority | Отдельные решения и fail closed; два synthetic human actors, no service self-approval | Реальные роли/self-approval OWNER DECISION REQUIRED; finance вне пилота |
| 11 Copy/staging | Existing snapshot автоматически запускает copy; encrypted staging другой fork | Не объявлять существующий путь no-copy, отдельный owner gate/интеграция | Reference-only negative fixture не runtime доказательство; ALIGNED PROPOSAL |
| 12 ТЗ | Титул v5.1 + v5.4 appendices, UUID и AUTO MVP5/MVP6 | Явное текущее задание задаёт узкий docs slice, additions v5.4 архитектуру | Редакционная консолидация ТЗ OWNER DECISION REQUIRED; DOCX не редактировался |

## ADR-08 — Narrow AUTO task.internal.create

Статус: **OWNER DECISION REQUIRED**. Проблема: Strategic Trust §8 относит
организационные autonomy policies к MVP6, §9B требует AUTO internal task в MVP5.
Варианты: (A) полный policy engine сейчас — расширяет scope; (B) только CONFIRM —
оставляет §9B открытым; (C) narrow server grant для одного LOW action/scope.
Предложение на рассмотрение: C после стабильного CONFIRM, выключено по умолчанию,
ограниченные tenant/project/actor, срок и квота, verified fresh evidence,
DB-only effect, atomic quota и аудит policy enable. Никакого внешнего/финансового
эффекта. Owner определяет роли, expiry, quota и self-approval; пример 5/час
в исходном пакете не действующее правило. Сейчас действует B: AUTO не включён,
не реализуется и §9B НЕ закрыт. Отказ от C не отменяет разрешённый CONFIRM срез.

## Открытые решения и точные стопы

| ID / владелец | Что требуется | Что блокирует до решения |
|---|---|---|
| O1 Product/security | AUTO ADR-08, полномочия enable, квота/expiry | Только AUTO implementation/приёмку |
| O2 Security/data owner | Metadata/fragment/derive rights, freshness TTL, processing/backup locations, retention/legal hold | Реальные source materialization и dispatch; synthetic fixture не универсальная policy |
| O3 Product/security | Кто approve/review, допустимость self-approval, role epoch writer | Real CONFIRM rollout; fixture использует отдельного approver |
| O4 Integration owner | Account subject validation, namespace registry, unresolved mapping procedure | Real mailbox cutover/cross-project reads |
| O5 Domain/integrator | DB-only Task effects, authority lock participation, audit extension physical design | Coding handoff с утверждённым transaction API; runtime gate |
| O6 Product/TZ owner | Редакционная версия ТЗ, точность deadline/time-of-day | Full TZ acceptance, time-specific Task contract |

Связь с исходными открытыми вопросами: Context I-01/03/04/08/11/12 и Action
Q05/06/07/12 согласованы здесь как proposals; I-02/05/06/07/10/13 и
Q02/03/04/08/09/10/11/14 требуют implementation/owner gates O2–O6.
Q01=O1, Q13 finance вне slice, Q15=O6. Source вопросы identity/ACL/retention/
staging сопоставлены O2/O4/ADR-11; historical audits не переписываются.
