# SourceReference / Evidence — v5.4, внутренний черновик

Стыки общего пилота уточняет [integration contract](../integration/README.md).
Общие ID/версии/owners определены там. Исходные standalone примеры сохранены
для истории design proposal; AUTO/send/reply/escalation не входят в первый срез.
Единственный интегрированный wire-пример — [pilot.json](../integration/pilot.json).

Статус: **DRAFT / NOT APPROVED**. Контрактный этап, не реализованный API и
не разрешение на перенос/копирование данных. База аудита:
`66129dca3a4cb92f9f09bd87f19f5433ceeb87a0`.

## Состав

- [Поля, схема и инварианты](contract.md).
- [Жизненный цикл, API и миграция](lifecycle-api-migration.md).
- [Матрица приёмки и зависимости](acceptance-integration.md).
- [Синтетические JSON-примеры](examples.json).
- [Карта existing → reuse → gap и доказательства](../../../audits/v54-source-evidence-contract.md).

Владелец этого контракта — только SourceReference и Evidence, включая
подчинённые observations/versions и descriptors разрешённых representations.
ContextRelation, Approval, Execution и Action Ledger не моделируются здесь:
используются только ID и требования к взаимодействию с их владельцами.
Нового storage engine, adapter, ORM, миграции или изменения integer PK нет.

## Основание и прочтение ТЗ

Источник: `C:/Users/dpush/Downloads/PU_Workspace_TZ_v5_4_FEDERATED_EVIDENCE_AUTONOMY.docx`.
SHA-256: `af7bfde75715345e4f32b9d7ca057812cdba7b8d8e0b6a1b105dfe20fc0d5df3`.
Прочитан весь основной OOXML body: 579 paragraphs, включая ячейки 10 таблиц.
В документе нет tracked insertions/deletions, comments/footnotes/endnotes parts.
Номера ниже — индексы абзацев XML с нуля, **не номера страниц**.
Текст титула всё ещё содержит «Версия 5.1», новые вставки явно называют v5.4.
Это зафиксированная неоднородность источника, а не основание отменять v5.4.

| Требование источника | Где учтено |
|---|---|
| §8, p108: UUID/org/version | Совместимые integer PK + опциональный public_id; новая запись не требует замены старых ключей |
| §§9–12, p109–148: snapshot metadata, revisions, производный cache | SourceVersion/representation, сила проверки и invalidation |
| §§14–16, p184–209: extraction, confidence, AI/privacy | Typed locators, provenance, untrusted content, read/retain policies |
| §§19–23, p226–254: audit, access, retention, recovery | History pointers, purge/tombstone, acceptance matrix |
| §31 Provider-Agnostic, p319–329 | Stable account identity, capabilities, organization residency |
| Context → Action → Human Control, p339–351 | IDs соседних сущностей, evidence != approval, payload/source fencing requirement |
| Federated Source-of-Truth, p361–363 | Original can stay remote, no mandatory cache or staging |
| Evidence Engine, p364–366 | Exact source/version, granular locator, verification status |
| Autonomy/Memory/Ledger, p367–378 | Evidence cannot grant autonomy; memory remains policy-scoped derived data |
| Приёмка v5.4, p383: A/F/G | Версия срока; без запрещённой копии; stale/unavailable отображаются явно |
| p381, §§32–40 | Сохранить рабочие модули; только контрактный scope текущего задания |

Старые «не входит Gmail/финансы/OCR» не трактуются как команда удаления.
Никакой текст документа, письма, OCR или evidence не является полномочием
AI, approval, политикой доступа либо разрешением выполнить внешнее действие.

## Главные решения на согласование

1. Идентичность источника — organization + стабильное подключение/аккаунт +
   provider namespace + external ID/incarnation; не имя, путь, email или hash.
2. DocumentVersion (версия импортированного текста) и SourceVersion (наблюдение
   конкретного оригинала) не отождествляются задним числом.
3. Evidence неизменно ссылается на SourceVersion. Перепроверка/доступность —
   отдельное изменяемое состояние; новое извлечение не переписывает историю.
4. Cache, staging, export и safe-copy не становятся оригиналом автоматически.
5. При отсутствии достаточной версии/проверки — unverified/unknown,
   не выдуманный hash/revision и не повышение автономности.
6. Сначала additive references и shadow reads. Переключение поведения,
   copy policy и backfill требуют отдельного утверждения интегратора.
