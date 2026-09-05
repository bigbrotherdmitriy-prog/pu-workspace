# v5.4 — единый контракт первого пилота

Статус: **NEEDS DECISIONS / документация, не реализация**. База:
`66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`. Дата: 2026-09-03.

Этот пакет разрешает стыки трёх design proposals. Для совместной реализации
общие определения ниже имеют приоритет над shorthand и расширенными примерами
исходных пакетов. Их специфические ограничения и исторические аудиты сохраняются.
Это не утверждение владельцем всех продуктовых политик и не PRODUCT PASS.

## Читать в таком порядке

1. [Общие типы, версии, identity и полномочия](glossary.md).
2. [Единственные writers, транзакции и lifecycle](ownership-transactions.md).
3. [ADR и открытые решения](decisions.md).
4. [Сквозной синтетический пример](pilot.json).
5. [Единая приёмка](acceptance.md).
6. [Миграции и следующая волна](migration-handoff.md).
7. [Отчёт интеграции](../../../audits/v54-contract-integration.md).

Исходные пакеты: [Source/Evidence](../source-evidence/README.md),
[Context/Communication](../context-communication/README.md),
[Action Trust](../action-trust/README.md). Общая ObjectRef определена только
в glossary; других независимых wire-схем для неё нет.

## Исполняемый срез, который предстоит реализовать

Синтетическое письмо с одним вложением → две SourceReference и их observations →
Evidence срока → DeadlineClaim и hypotheses проекта/договора → ручная проверка
claim и context CAS → предложения задачи и черновика → approval точной task revision →
одна внутренняя Task/receipt/ledger → отдельный proposal/approval отмены →
cancel receipt/ledger. Черновик не утверждает, что ещё не созданная задача создана.

CONFIRM — default для create/cancel; подготовка черновика — ASSIST/DRAFT,
не EXECUTE. Первый срез не отправляет письма, не создаёт ResponseExpectation,
не исполняет escalation, финансы или provider mutation. UNKNOWN/reconciliation
проверяется отдельно fake-provider тестами контракта, не реальным Gmail.
AUTO выключен; требование AUTO-приёмки ТЗ остаётся открытым (ADR-08).

Не входят новые providers, graph database, Company Memory, OCR-алгоритмы,
массовый backfill, автоматическая смена copy-policy и интеграция staging-форка.
Существующие Google/Gmail/Telegram/Яндекс/OCR/финансы сохраняются.

## Проверка документации

Из корня этой worktree: `python docs/architecture/v54/integration/validate.py`.
Скрипт только читает Markdown/JSON и проверяет синтетические связи/хеши;
не подключает БД, сеть или продуктовые модули. Acceptance matrix — будущие
исполняемые тесты; успешная проверка примера их не заменяет.

JSON records — минимальные проекции для сквозного сценария, не полные create DTO
каждого владельца. Они не отменяют обязательные source policy/locator/provenance
поля исходного Source contract. Fixture предполагает разрешённый synthetic доступ;
в реальном resolver отсутствие этих policy полей блокирует операции.
