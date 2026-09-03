# Единая матрица будущей приёмки

Это тестовый план, **не результаты runtime**. Первичный владелец каждого
сценария один; исходные SE/P/N сценарии остаются детализацией, не отдельным счётчиком
готовности. Общая synthetic fixture — pilot.json. Никаких реальных providers.

| ID | Сценарий / действие | Обязательное наблюдение | Владелец |
|---|---|---|---|
| INT-01 | Письмо+1 attachment → review context/claim → approve → create → approve cancel | 2 sources, 1 claim, 2 confirmed context links, 1 Task затем cancelled; 2 receipts, trace до evidence; draft не sent | Интегратор |
| INT-02 | Same account refresh / другой account / same external ID в другом namespace | Refresh identity прежняя; другой account/source отдельные; stale job не переключается | Identity |
| INT-03 | Source bytes изменились, TTL expired, source недоступен | Старые pins сохранены, EXECUTE=0; unknown version не fresh | Evidence |
| INT-04 | Только last_checked_at/assessment version обновлены, original тот же | Hash/approval прежние, live recheck выполнен; same grant допустим только до expiry | Trust+Evidence |
| INT-05 | Изменить payload/assignee/target/claim/evidence/policy/revision после approval | Новый hash/freeze; старый grant неприменим; effects=0 | Trust |
| INT-06 | Revoke mailbox/source/project/approver права до dispatch и гонка revoke | Revoke до linearization → 0 effects; dispatch раньше → честный outcome; никакого admin fallback | Auth+Trust |
| INT-07 | Duplicate ingress, replay/new model/retry job с тем же claim anchor | 1 Message/intent/action/Task, manual/rejected context не перезаписан | Communication |
| INT-08 | Два workers/разные jobs и command keys одного action | Одна business reservation/receipt; old fence не commit; hash mismatch key→conflict | Trust |
| INT-09 | Два concurrent context confirm/correct разных проектов | Один winner CAS; project+contract+audit атомарны; loser conflict, история сохранена | Context |
| INT-10 | Crash до/после intent commit, до enqueue, после enqueue до отметки | Durable pending recovery с тем же key; terminal job не обходится новой identity | Trust/queue |
| INT-11 | Crash после Task mutation до receipt/ledger/commit; crash после commit до job complete | До commit всё rollback; после всё присутствует; retry только читает receipt | Task+Trust |
| INT-12 | Crash consumer receipt→communication.task | Recovery relation по receipt unique, Task count не растёт | Context |
| INT-13 | Fake external dispatch timeout/lease expired/пустой поиск/поздний receipt | UNKNOWN, 0 повторных mutate; authoritative proof разрешает outcome, NOT_APPLIED нужен для retry | Trust fake-provider |
| INT-14 | Reference-only запрещает bytes/temp/OCR/cache/staging/quote | Ни одного download/materialization; unresolved result, approval не обходит policy | Source |
| INT-15 | Email/OCR/model просит AUTO/send/сменить actor/policy | Только data, 0 tool/financial/external effects; safe audit без source text | Security |
| INT-16 | Cross-tenant refs, угаданный UUID, project без mailbox ACL | Не раскрываются object/count/snippet/cursor; запись отклонена | Все resolvers |
| INT-17 | Evidence ссылка есть, claim unverified / неоднозначный срок | Ни confirmation по confidence, ни executable task; ручной review отдельно | Task claim |
| INT-18 | Purge/revoke/tombstone + восстановление backup | Нет PII/quote в ledger/logs; восстановленные reads блокируются до replay policy | Source+Audit |
| INT-19 | Cancel без нового approval, Task version changed, уже published | 0 mutations; исходный receipt SUCCEEDED неизменен; valid cancel отдельный receipt | Task+Trust |
| INT-20 | Legacy approved/context_confirmed/null mailbox | Не фабриковать approver/identity; no backfill по active project | Интегратор |
| INT-21 | Старый Task/draft/Telegram route пишет pilot объект | Общий gate или deny; никакого второго writer/внешнего publish | Entry-point интегратор |
| INT-22 | Draft/reply/payment confusion, неизвестный capability | Draft != sent, reply != completed, context != paid; fail closed | Trust |
| INT-23 | AUTO requested без решения owner | BLOCKED, AUTO acceptance OPEN; после решения отдельный scope/quota race suite | Policy |

Для INT-06/08–12 нужны настоящие PostgreSQL транзакции, две workers и barriers,
crash injection, наблюдаемые DB counts и receipts. Fake provider счётчик вне
worker memory, потеря response не теряет effect count. SQLite/mock и зелёный job
не доказывают эти свойства. INT-14 отдельно требует проверки existing safe-copy
entrypoint до federated claim: нынешний docs fixture его не меняет.

Документационные проверки сейчас: синтаксис JSON, typed refs/tenant/pins,
canonical SHA-256, approval/receipt sequence, локальные Markdown links,
git diff --check. Не называть их прохождением INT-01…23.
