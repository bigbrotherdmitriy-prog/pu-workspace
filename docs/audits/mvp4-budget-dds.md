# MVP4: бюджет проекта и ДДС plan/fact

Дата: 2026-09-05

База: `ef78cf0347f878b378ee6f42a60543aa0366f7e2`

Ветка: `codex/mvp4-budget-dds`

## Результат

Реализован безопасный срез M4-05/M4-06: строки бюджета и события ДДС связываются с проектом, договором, этапом ГПР или задачей, первичным документом и его точной `DocumentVersion`. При наличии v5.4 Evidence сохраняются точные pin-поля `evidence_id`, `evidence_revision` и `evidence_assessment_version`.

Поступление заказчика и выплата поставщику представлены одним реестром с направлениями `inflow` и `outflow`. Предложение и подтверждённый план не являются фактом оплаты. Факт создаётся только отдельной командой пользователя с ролью manager. Банковская выписка не требуется и банковская интеграция не вызывается.

## Модель и lifecycle

- `BudgetLine`: proposal → approved/active/closed либо rejected;
- `CashFlowEntry`: proposal → approved → received/paid либо cancelled;
- low-confidence или непроверенный источник получает `review_status=required`;
- manager отдельно подтверждает контрольные связи и только затем может подтвердить факт;
- факт хранит точную сумму, дату, пользователя, время и `record_version`;
- каждое подтверждение/исправление добавляет неизменяемый `CashFlowFactHistory`;
- факт ДДС пересчитывает plan/fact соответствующей строки бюджета, но не выполняет платёж.

## Fail-closed инварианты

- проект и каждая связанная сущность проверяются на один scope;
- этап ГПР не может относиться к другому договору;
- первичный документ обязан иметь точную `DocumentVersion`;
- Evidence должен указывать на текущую доступную SourceVersion того же проекта и документа;
- низкая уверенность никогда не создаёт факт автоматически;
- plan approval и payment confirmation — разные действия;
- повтор того же подтверждения идемпотентен и не создаёт вторую запись истории;
- другая сумма или дата повторного подтверждения возвращает `409`;
- корректировка требует CAS по `record_version`, прежней сумме и прежней дате;
- свободный текст причины не попадает в audit;
- update/delete истории фактов запрещены ORM guard;
- никаких external action, AUTO, payment dispatch или provider call нет.

## API и интерфейс

Расширены существующие endpoint-ы `/execution/budget`, `/execution/cash-flow`, `/execution/invoice-proposals`, structured import и overview; второй финансовый контур не создавался. В ответе overview видны контрольные связи, точная версия документа, confidence, review status и версия записи.

Интерфейс различает «Подтвердить план» и «Подтвердить оплату — факт пользователем», показывает поступления заказчика отдельно от выплат и передаёт `record_version` для подтверждения/корректировки. Для ручного создания бюджет/ДДС требуют договор, ГПР, бюджетную строку и первичный документ до отправки.

## Миграция

- новая последовательная revision: `a54f001c0a14`;
- parent: `a54f001c0a13`;
- добавлены control/evidence/CAS поля к существующим таблицам;
- добавлена `cash_flow_fact_history`;
- все readiness/CI pins обновлены до `a54f001c0a14`;
- ожидается ровно одна Alembic head.

## Проверки

- regression-тесты нового среза: `16 passed`;
- новый срез вместе с существующими finance/daily тестами: `31 passed`;
- frontend targeted: `2 passed`;
- полный frontend Vitest: `107 passed`;
- frontend TypeScript check: PASS;
- frontend production build: PASS;
- Alembic offline SQL `a13:a14`: PASS;
- затронутые CI schema/runtime contract tests: `19 passed`;
- полный backend: `1248 passed, 19 skipped`; единственный timing smoke при параллельной нагрузке превысил 10 с (`14.85 s`), его изолированный контрольный прогон: `1 passed in 5.48 s`;
- `git diff --check`: PASS.

PostgreSQL concurrency считается `CONDITIONAL`: отдельный `TEST_POSTGRES_DSN` недоступен, а SQLite не доказывает семантику `SELECT ... FOR UPDATE`. Пропуски полного backend относятся к PostgreSQL/live-dependent сценариям.

Общий `scripts/ci` прогон дал `150 passed` и три существующих Windows/bash path failures: кириллический сегмент временного пути по-разному декодировался в Python и bash. Затронутые этим коммитом CI-контракты отдельно проходят; это не засчитано как полный CI PASS.

## Изменённые зоны и интеграция

- finance ORM/API и daily briefing;
- одна последовательная Alembic migration;
- существующий finance controller/component и его тест;
- schema/readiness/runtime pins;
- regression-тесты и этот аудит.

Ожидаемые пересечения при интеграции: `frontend/src/App.tsx`, `backend/app/schema.py`, CI/runtime schema pins и Alembic head. Миграцию нельзя переносить до `a54f001c0a13`; если интеграционная ветка уже получила новую head, revision необходимо последовательно перебазировать, не создавая вторую голову.

## Ограничения

- реальная PostgreSQL-конкурентность должна пройти в изолированном runtime CI;
- legacy записи без контрольных ссылок остаются читаемыми, но fail-closed не подтверждаются как план или факт;
- выбор первичного документа в текущем UI опирается на существующий document-candidate flow; отдельный универсальный picker в этом срезе не добавлялся;
- мультивалютный пересчёт и курсовые разницы не реализованы; бюджет сохраняет существующее поле currency, ДДС наследует валютный контекст связанной строки;
- закупки, версии поставок/актов и explainable forecast относятся к следующим срезам M4-07..M4-09;
- production, production DB, secrets и реальные финансовые документы не изменялись.
