# Аудит и проект action trust contract v5.4

Дата: 2026-09-03. Результат: **документационный контракт, не реализация**.
Статус предложений: PROPOSED / OWNER DECISION REQUIRED.

## Исходное состояние до изменений

- Основная worktree: `C:\Users\dpush\OneDrive\Документы\ChatGPT\Workspace\pu-workspace-commercial-p2-yandex360`.
- Её ветка: `codex/commercial-p2-yandex360`, HEAD
  `83774aac726acd4e27b349e9194f30783158bde8`.
- Незакоммиченные файлы: `backend/app/api/auth.py`, `backend/app/api/local_upload.py`,
  `backend/app/api/workspace.py`, `backend/app/schema.py`, `backend/app/static/app.js`,
  `docker-compose.yml`, `frontend/index.html`. Не копировались, не редактировались,
  не включены в commit этого задания.
- Точная указанная база доступна локально:
  `66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.
- Создана отдельная чистая worktree:
  `C:\Users\dpush\OneDrive\Документы\ChatGPT\Workspace\pu-workspace-v54-action-trust-contract`.
- Ветка: `codex/v54-action-trust-contract`; начальный HEAD равен точной базе,
  начальный status чистый. Существующие ветки/worktree не сбрасывались.
- Применимых AGENTS.md в проверенных родительских каталогах и дереве базы не найдено.

## Источник требований

Прочитан пользовательский DOCX
`C:\Users\dpush\Downloads\PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`.
SHA-256: `AF7BFDE75715345E4F32B9D7CA057812CDBA7B8D8E0B6A1B105DFE20FC0D5DF3`.

Прочитан основной document.xml (569 непустых текстовых абзацев), включая
таблицы, в исходном порядке. Использован read/review-порядок skill documents;
оригинал DOCX не менялся. Это содержательное чтение, не проверка Word-вёрстки,
страниц, comments или tracked changes. Результат нужен в Markdown, новый DOCX
не создавался и его rendering не заявляется.

Опорные части: основные §§3, 7, 15–16, 18–22; дополнения «Context → Action → Human
Control» / Human Approval & Audit и «Strategic Trust & Enterprise Intelligence
Layer» §§3–4, 7–9. Титул внутри файла всё ещё называет v5.1 Implementation Ready,
дополнения описывают v5.4. Это редакционная неоднозначность источника, а не повод
игнорировать явно перечисленные пользователем v5.4 инварианты.

ТЗ относит расширенные organizational autonomy policies к MVP6, но сценарий
приёмки MVP5 требует AUTO для internal task и CONFIRM для external send. Решение
предложено в [ADR](../architecture/v54/action-trust/README.md): narrow server
allowlist MVP5, полноценные организационные политики MVP6. **Владелец не утвердил**;
до решения AUTO не включать и acceptance не считать выполненной.

## Findings до проектирования

Полная [existing → reuse → gap карта](../architecture/v54/action-trust/README.md)
содержит ссылки на проверенные исходники. Ниже ключевые выводы статического аудита.

| ID | Подтверждение в базе | Вывод для контракта |
|---|---|---|
| F01 | OrganizerProposal/Action/Operation, TaskHistory, ResponseDraft, CashFlowEntry и BackgroundJob уже существуют; отдельного класса ChangeBatch/ActionProposal/Approval/ActionLedger не найдено | Не второй engine: facade и versioned binding над существующими доменами; ChangeBatch сопоставить OrganizerProposal |
| F02 | `api/responses.py:update_draft` меняет subject/body, а status меняет только если он передан | Одобренный черновик может сохранить approved после редактирования; требуется immutable revision и новое approval |
| F03 | `api/gmail.py:send_gmail` проверяет approved/sent_external_id, но получает адресата и рендерит письмо при отправке | Approval должен связывать финальные recipient/account/content/thread/attachments, не только mutable draft ID |
| F04 | Там же provider send предшествует записи sent_external_id и commit; business CAS/reservation нет | Возможны concurrent sends и неизвестный исход после сбоя. Lease/job key этого не исправляет |
| F05 | `OrganizerRepository.reconcile_operation` обновляет before_json/after_json и сбрасывает rolled_back_at при конфликте ключа | Operation — mutable projection, не append ledger; сохранять её и добавлять неизменяемые audit events |
| F06 | Executor имеет preflight/source recheck/scope guards; rollback возвращает before после проверки области | Сохранить guards, но не заявлять version-safe undo без проверки текущего after и provider capability |
| F07 | `task_engine.create_tasks_from_files` создаёт assigned Task с needs_review и связанную Obligation, выбирая default assignee как creator; commit внутри | Proposal ≠ назначение задачи. Для pilot нужен DB-only helper без скрытого Obligation; существующая Obligation сама по себе не доказательство финансового платежа |
| F08 | `api/tasks.py:update_task` после commit вызывает publish_actions для external_action_status=executed | Internal cancel pilot не должен неявно публиковать внешнее обновление |
| F09 | ProjectAIPolicy регулирует AI data egress; Organizer имеет legacy auto-copy правила; automation runs имеют дедуп по расписанию | Ни один механизм сам по себе не v5.4 autonomy grant. Сохранить и применять как независимые ограничения |
| F10 | `confirm_payment` требует manager, принимает human fact, возвращает already_confirmed, по умолчанию берёт planned amount/сегодня; отдельный requisites_status | Реквизиты/обязательство/факт разделить; точные суммы/даты фиксировать до approval; выписку не делать обязательной |
| F11 | AuditLog: action/entity/details/time без обязательных actor/tenant/revision/decision полей | Расширение через существующий audit writer; append-oriented DB contract пока не доказан |
| F12 | Queue имеет durable key/leases/fencing; enqueue сам commit | Business intent/receipt требуют отдельной идемпотентности и согласования transaction owner/pending dispatch |

Это выводы по исходникам на указанной базе, не воспроизведение ошибок production
и не утверждение о фактических двойных отправках. Gmail, Google Drive, Telegram,
AI Secretary, финансовые проверки и очередь не изменялись и не запускались.

## Подготовленные материалы

| Файл | Содержание |
|---|---|
| [README](../architecture/v54/action-trust/README.md) | Карта reuse/gap и предложенный ADR MVP5/MVP6 |
| [contract.md](../architecture/v54/action-trust/contract.md) | Facade, независимые оси, state machines, 12 инвариантов, canonicalization, логические records, dispatch/reconciliation |
| [policy-pilot.md](../architecture/v54/action-trust/policy-pilot.md) | Матрица policy, CONFIRM/AUTO internal task, audited cancel, irreversible send и финансовые границы |
| [examples.json](../architecture/v54/action-trust/examples.json) | Синтетические envelopes, hashes, policy decisions, approvals и ledger; сценарии edit invalidation/UNKNOWN/corrective send |
| [negative-scenarios.md](../architecture/v54/action-trust/negative-scenarios.md) | 42 негативных сценария с наблюдаемыми результатами для будущих тестов |
| [rollout.md](../architecture/v54/action-trust/rollout.md) | Поэтапный cutover без второго engine, transaction gaps и 15 вопросов интегратора |
| Этот отчёт | База, scope, выводы и границы проверки |

Минимальные schema records — предложение без SQL/Alembic: immutable proposal
revision, policy revision/decision, approval events, execution projection/attempt,
ledger extension к AuditLog. SourceReference/Evidence и ContextRelation только
по ID/version; их модели не дублируются.

## Проверки документационного пакета

Локальная проверка документации и JSON выполнена bundled Python, exit code 0:

- Все 4 policy hash и 4 envelope hash совпали с SHA-256 канонического JSON.
- Все 3 human approvals и 4 policy decisions совпали с revision/hash/target/
  evidence/policy bindings; AUTO не содержит фиктивного human approval.
- Все 10 ledger events имеют согласованные bindings, последовательности и время;
  cancel approval следует за созданием target, dispatch укладывается в срок grant.
- Подмена payload, revision, actor, target version и evidence version меняет hash;
  changed-payload example запрещает использование прежнего approval.
- UNKNOWN-примеры запрещают automatic mutation retry; corrective email не
  объявляет отзыв оригинала. Эти проверки проверяют примеры, не runtime guards.
- Все 35 локальных Markdown-ссылок разрешились; JSON не содержит duplicate keys,
  float, невалидных строк или integer вне принятого безопасного диапазона.
- Найдены ровно 42 уникальных, последовательно нумерованных negative scenarios.
- `git diff --cached --check` — без ошибок; staged diff содержит только 7 файлов
  разрешённого документационного scope. Product code, migrations, queue и settings
  в diff отсутствуют. Повторная проверка основной worktree показала прежние
  HEAD/ветку и тот же список 7 пользовательских изменённых файлов.

Диагностический итог: `DOCUMENT_CHECKS_PASS assertions=499; policy_hashes=4;
envelope_hashes=4; approval_records=3; ledger_events=10; local_links=35;
negative_scenarios=42`. Большая часть assertions — проверки структуры и bindings;
это не 499 продуктовых regression-тестов.

Минимальная воспроизводимая проверка хешей из корня worktree:

```python
import hashlib, json
from pathlib import Path
d = json.loads(Path("docs/architecture/v54/action-trust/examples.json").read_text(encoding="utf-8"))
def sha(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
assert all(sha(p["policy"]) == p["policy_sha256"] for p in d["policies"])
assert all(sha(a["envelope"]) == a["envelope_sha256"] for a in d["sealed_actions"])
```

Продуктовые unit/integration tests и реальные конкурентные provider/PostgreSQL
сценарии в рамках design-only задачи не запускаются; 42 сценария — спецификация
будущей приёмки, не число прошедших тестов. Миграций и runtime implementation нет.

## Открытые ограничения и handoff

- Contract и server AUTO policy не реализованы; существующие legacy gaps не
  устранены этим документационным commit. Процент реализации v5.4 не заявляется.
- Требуются владельцы authority epochs, evidence freshness/ACL API, target CAS,
  DB transaction owner, ledger permissions и UI подтверждения frozen revision.
- Gmail/provider idempotency и authoritative reconciliation не доказаны:
  UNKNOWN нельзя автоматически превращать в resend. Поздний revoke не отзывает
  уже отправленный провайдерский запрос; необходим честный may_have_executed.
- Cancel созданной задачи — COMPENSATABLE, не стирание истории. REVERSIBLE
  требует доказанных preconditions; письмо IRREVERSIBLE и corrective send отдельно.
- Нужны решения о self-approval, AUTO scope/expiry/quota и редакционной иерархии ТЗ.
  Список точных вопросов — в rollout; проект лимита 5/час не утверждённая policy.
- Следующий исполнитель внедряет только согласованный узкий Task pilot, сохраняя
  общие регрессии. Domain finance/Organizer/external send cutover — отдельные этапы.

Этот пакет разрешён только в `docs/architecture/v54/action-trust/**` и данном
audit-файле. Push, merge, PR, production deploy и изменения production не выполнялись.
