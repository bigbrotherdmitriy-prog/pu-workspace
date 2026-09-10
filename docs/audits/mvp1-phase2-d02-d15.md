# MVP-1 Phase 2 — D02–D15

Дата реализации: 2026-09-09

Дата PostgreSQL runtime-приёмки: 2026-09-10

Ветка приёмки: `codex/mvp1-phase2-review`

База: `e1bf2194bb9fc479dc802144659cb265254fbf12`

## Решение

Реализация критериев D02–D15 закрыта на уровне кода и синтетических
регрессионных тестов. Внешние действия остаются fail-closed. Google mutation
runtime выключен по умолчанию и требует явного
`PU_MVP1_GOOGLE_LIVE_MUTATIONS=true`; внешний vision также выключен по
умолчанию. Ни один критерий в этом отчёте не имеет статуса
`live_provider=verified`.

Итоговый статус этапа: **IMPLEMENTATION CLOSED / LIVE PROVIDER OPEN /
MVP-1 POSTGRES RUNTIME PASS / HISTORICAL SUITE HAS KNOWN MVP-4 FAILURE**.

## Implementation-closed

| Критерий | Реализованный результат | Проверка |
|---|---|---|
| D02 | Immutable metadata envelope: source ID/path/parents, MIME, size, time, checksum, provider revision, metadata hash, URL, availability, ACL, analysis state и shortcut target; API возвращает полный безопасный envelope. | `test_mvp1_snapshot_metadata_envelope.py` |
| D03 | Сохранён существующий incremental snapshot/durable recovery; новые поля добавлены последовательными миграциями без второй head. | snapshot recovery + полный backend |
| D04 | Shared Drives flags, shortcuts без неявного перехода к target, полная пагинация, bounded retry только чтений для 429/5xx и Google 403 rate-limit reasons. | `test_mvp1_google_metadata_contract.py` |
| D05 | Docs → DOCX/PDF, Sheets → XLSX/CSV, Slides → PPTX/PDF; pre/post revision pin, SHA-256, лимит размера и совместимость с encrypted native-export cache. | `test_mvp1_native_export_transport.py` |
| D06 | Версионированный порядок policy → project → organization → manual → metadata → AI → default; persisted rules теперь несут source и production classifier не зависит от порядка ID; стандартное имя идемпотентно. | `test_mvp1_classification_precedence.py` |
| D07 | Exact preview/approval pipeline подключён к Google conditional adapter: exact state, ancestry, version+ETag token, `If-Match`, отсутствие retry мутаций, 409/412 → `conflict_source_changed`. | `test_mvp1_google_conditional_mutation.py`, mutation runtime tests |
| D08 | Сохранены существующие durable job, idempotency key, attempt fence, receipt и reconciliation; job payload содержит только ID/CAS/operation. | storage mutation acceptance/runtime/wiring suites |
| D09 | Сохранены immutable mutation ledger и compensating rollback; live-test расширен конфликтом внешнего изменения и возвратом тестовой копии. | `test_mvp1_google_storage_live.py` — код готов, live запуск пропущен |
| D10 | Keyset pagination до 500 строк, server-side state filters, React virtual window ≤18 DOM-строк на наборе 10 000, bulk selection загруженной страницы и явная дозагрузка. | backend envelope tests, `SnapshotVirtualTree.test.tsx` |
| D11 | Google managed root/children получают opaque ownership/source markers; retry продолжает частичное дерево по source marker; cleanup проверяет ownership всего subtree и отказывает при чужом объекте. | `test_mvp1_google_managed_copy.py`, cleanup fencing suites |
| D12 | Сохранены DocumentVersion, dedup/comparison/retention и exact source-version contracts. | существующие version/retention regression tests |
| D13 | Сохранено безопасное extraction поддержанных форматов с bounded processing и human review boundary. | content/extraction regression tests |
| D14 | Сохранены XLSX formula/cached value, durable materialization и exact cell locators. | XLSX durable/cell/retention suites |
| D15 | Реализован и подключён к MVP-1 snapshot extraction policy router: local baseline, `auto/ocr/vision/both`, capability report, incomplete reason; bytes могут уйти наружу только в явно разрешённый `AIProviderAdapter`. Низкая уверенность остаётся human-review. | `test_mvp1_ocr_vision_routing.py` |

## Ожидают live-provider подтверждения

Следующие пункты **не запускались и не считаются подтверждёнными**:

1. Phase 1c OAuth port: реальный authorization callback, refresh и выбор токена
   по exact `connection_id`.
2. D04: Shared Drive, shortcut и provider rate-limit поведение на тестовом
   Google-аккаунте.
3. D05: все шесть native export вариантов с живой provider revision.
4. D07/D09: rename/move по exact ETag, реальный
   `conflict_source_changed`, compensating rollback.
5. D11: частично созданная managed copy, повторный запуск и ownership cleanup
   на тестовой папке.

Единственная команда будущего live-прогона (не выполнялась):

```powershell
$env:PU_MVP1_GOOGLE_LIVE_TEST = "1"
python -m pytest -q backend/tests/test_mvp1_google_storage_live.py
```

До явного подтверждения владельца результата этой команды все перечисленные
пункты имеют `live_provider=not_verified`.

## Схема и конфигурация

- `a54f001c0a23`: metadata envelope.
- `a54f001c0a24`: shortcut target metadata.
- Единственная ожидаемая Alembic head: `a54f001c0a24`.
- `CURRENT_SCHEMA_REVISION`, readiness и CI pins обновлены до `a54f001c0a24`.
- `PU_MVP1_GOOGLE_LIVE_MUTATIONS=false` — безопасное значение по умолчанию.

## Проверки

- Финальный изолированный PostgreSQL/CI run:
  [GitHub Actions #41](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/34442787786),
  commit `f2e0f37052a5e53c4bb737de202f2db28d6f14ae` — **PASS**.
- Все job финального run: `lint`, `local-engines`, `backend-offline`,
  `runtime`, `acceptance-gate` — **PASS**.
- Полный offline backend в run #41: `2540 passed, 63 skipped`, `0 failed`,
  `0 errors`, 403,28 с. PostgreSQL-проверки учитываются отдельным runtime-job.
- Полный frontend: `405 passed`.
- Frontend TypeScript check: PASS.
- Frontend production build: PASS; сгенерированный `react_dist` исключён из diff.
- Alembic heads: одна, `a54f001c0a24`.
- CI contract/harness: `384 passed`, `0 failed` из ASCII-only temp path.
- `git diff --check`: PASS.
- `actionlint`: локально недоступен.
- Миграции в run #41: базовая, storage, automation, MVP-2, MVP-3, MVP-4,
  authority, materialization и schema fixture применены до единственной head
  `a54f001c0a24` — **PASS**.
- `postgres_mvp1_storage`: `2/2`, `0 skipped`, 6,39 с — **PASS**.
- `postgres_process_fault`: четыре обязательных process-kill/recovery
  сценария, `4 passed`, `0 failed`, `0 skipped`, 29,01 с — **PASS**:
  reclaim после lease, S07 intent recovery, T2 pre-commit rollback и S08
  receipt replay без повторного эффекта.
- Точечная проверка исправления materialization:
  [targeted PostgreSQL run #2](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/34441719589) —
  `1 passed`, `0 failed`, `0 skipped`, 2,16 с.
- Materialization в полном run #41:
  `postgres_materialization_migration` — `1/1`, 3,02 с;
  `postgres_materialization_runtime` — `1/1`, 3,83 с — **PASS**.
- Все 16 записей `mandatory_postgres` в безопасном протоколе run #41 имеют
  статус `PASS`; `postgres_abc_integration` — `302 passed`, `0 skipped`.
- Acceptance corpus и durable gzip regression — **PASS**.
- Очистка изолированной среды: `cleanup=PASS`, список неудалённых тестовых БД
  пуст.
- Известный WBS-дефект **не входит** в этот PASS. Только тест
  `test_pg_wbs_summary_constraint_rejects_leaf_intent` временно исключён на
  уровне CI-раннера со ссылкой на
  [mvp4-wbs-known-defect-summary-transition.md](mvp4-wbs-known-defect-summary-transition.md).
  Остальные шесть PostgreSQL WBS-проверок прошли. Constraint и WBS-логика в
  этой ветке не исправлялись.
- Live Google: NOT RUN.

## Ограничения

- PostgreSQL runtime gate относится к синтетическому MVP-1 контуру и не
  заменяет отдельный live-provider Google smoke.
- Google API может не вернуть пригодный ETag для конкретного live transport;
  тогда conditional adapter корректно откажет с
  `exact_provider_etag_unavailable`, а не выполнит небезопасную мутацию.
- Yandex-specific OAuth в Phase 1c намеренно не реализовывался. Google-specific
  live mutation/managed-copy доказательства нельзя переносить на Яндекс.
- External vision не активирован и не является live-проверенным.
