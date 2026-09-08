# PU Workspace: доказательная матрица готовности MVP1–MVP7

Дата аудита: 2026-09-08. Проверенный commit: `28ebe5fa6d8165fb257fa6e77a3d37302625c496`.

## Решение

**Ограниченный пилот: CONDITIONAL.** Уже можно начинать изолированный пилот на
синтетических или обезличенных данных: read-only анализ, ручная проверка и
подтверждённые внутренние действия. Нельзя считать готовыми работу на реальных
клиентских данных, live-изменения Google/Яндекс/Gmail/Telegram, production и
полную приёмку ТЗ. Для текущего SHA ещё нет единого доказательства PostgreSQL
runtime, live-provider sandbox, canary и production observation.

**Полное ТЗ: NOT READY.** В машинной матрице 78 критериев: 44 имеют полную
реализацию проверенного ограниченного контракта, 25 реализованы частично, 9 пока
не имеют требуемой реализации. Ни один критерий не получил `canary=verified` или
`production=verified` на этом SHA. Поэтому наличие большого числа unit-тестов и
успешных CI предков не называется полной готовностью.

## Как получены проценты

Единственный источник чисел — валидируемый файл
[v7-mvp-completion-matrix.json](v7-mvp-completion-matrix.json). Формула:

```text
100 × Σ(вес_критерия × вес_измерения × оценка_статуса)
      / Σ(вес_критерия × вес_применимого_измерения)
```

- оценка: `verified=1`, `partial=0.5`, `not_verified/blocked=0`,
  `not_applicable` исключается из числителя и знаменателя;
- вес критерия: `5` — обязательный/high-risk, `3` — P1/medium-risk,
  `1` — P2/опциональное расширение;
- измерения полного ТЗ: implementation `0.30`, exact-SHA runtime `0.25`,
  live provider `0.15`, canary `0.15`, production `0.15`;
- ограниченный пилот считает только implementation и runtime с теми же весами.

`partial` в runtime означает bounded/historical evidence, но не полный прогон
текущего SHA. Валидатор не разрешает поставить `verified` без evidence нужного
типа с точным `candidate_sha` и не разрешает объявить production без canary.

| Scope | Критериев | Ограниченный пилот | Полное ТЗ |
| --- | ---: | ---: | ---: |
| MVP1 | 15 | 54.5% | 30.0% |
| MVP2 | 11 | 59.8% | 32.9% |
| MVP3 | 11 | 59.4% | 38.4% |
| MVP4 | 8 | 63.5% | 41.1% |
| MVP5 | 13 | 77.3% | 49.3% |
| MVP6 | 11 | 37.2% | 20.5% |
| MVP7 | 9 | 68.8% | 43.5% |
| **Итого** | **78** | **60.2%** | **35.8%** |

Эти проценты показывают готовность доказательств по заданной формуле, а не долю
строк кода, календарное время или вероятность отсутствия дефектов.

## Трассировка каждого критерия

Полные title, weight, пять независимых статусов и пути evidence для всех строк
находятся в JSON. Ниже — компактный cross-check реализации; отсутствие пункта в
`нет` не означает runtime/live/canary/production PASS.

| MVP | Реализовано для ограниченного контракта | Частично | Требуемой реализации нет |
| --- | --- | --- | --- |
| 1 | D01, D03, D08, D12, D13 | D02, D04–D07, D09–D11, D14–D15 | — |
| 2 | M201–M202, M204–M205, M208–M210 | M203, M206 | M207, M211 |
| 3 | M301–M302, M304, M306, M308–M309 | M303, M305, M307, M310 | M311 |
| 4 | F01–F02, F04–F07 | F03 | F08 |
| 5 | T01–T13 | — | — |
| 6 | — | M6-01–M6-02, M6-05, M6-07–M6-09, M6-11 | M6-03, M6-04, M6-06, M6-10 |
| 7 | M7-01–M7-02, M7-04–M7-08 | M7-03 | M7-09 |

Особенно важно не смешивать пять столбцов:

- `implemented` подтверждает только наличие поведения в коде;
- `tested_runtime` требует реальной БД/процессов на точном SHA;
- `live_provider` требует разрешённого test account и наблюдаемого provider
  effect/reconciliation;
- `canary` требует изолированной ограниченной эксплуатации и rollback;
- `production` требует отдельного deployment approval и observation evidence.

## Источники и редакционная граница

Авторитетная трассировка исходного DOCX —
[tz-final-coverage-map.md](tz-final-coverage-map.md), SHA-256 исходника
`af7bfde75715345e4f32b9d7ca057812cdba7b8d8e0b6a1b105dfe20fc0d5df3`.
Поздние изменения проверены по текущему коду, тестам и отчётам v7, включая
[v7-next-acceptance-checkpoint.md](v7-next-acceptance-checkpoint.md),
[v7-wbs-backend.md](v7-wbs-backend.md),
[v7-finance-forecast-backend.md](v7-finance-forecast-backend.md) и
[v7-ci-repair-integration.md](v7-ci-repair-integration.md).

Есть две несовместимые исторические подписи «MVP6». В полном v5.4 scope MVP6 —
федерация/enterprise adapters (`M6-01..M6-11`). Файл
[mvp6-document-control.md](../mvp6-document-control.md) — более ранний
документный инкремент и используется только как supporting evidence, а не как
замена определения MVP6. MVP7 AI Secretary — позднее явно добавленный scope из
[mvp7-ai-secretary-control.md](../mvp7-ai-secretary-control.md); его девять строк
включены в общий текущий denominator и не выдаются за исходные пункты DOCX v5.4.

## Что закрывать дальше

### Чтобы перейти от CONDITIONAL к реальному пилоту

1. Запустить полный PostgreSQL/process-fault и browser набор на **этом же SHA**;
   WBS migration/runtime и finance concurrency не переносить из старого SHA.
2. На разрешённых тестовых аккаунтах проверить nested storage read/reconnect,
   Gmail origin/dedup/attachment и один CONFIRM provider effect с UNKNOWN
   reconciliation. Не использовать production credentials.
3. Выполнить isolated canary: allowlist cohort, backup/restore, kill switch,
   rollback, safe logs/metrics и зафиксированное окно наблюдения.

### Конкретные implementation gaps полного scope

- MVP2: inbound Tasks/Calendar reconciliation (M207), evidence-cited Q&A (M211).
- MVP3: единый evidence/CAS legacy mutation path (M311); частично остаются
  escalation, meeting origin, external digest и самостоятельная Company.
- MVP4: versioned currency/VAT/retention/partial-payment policy (F08); F03 до
  решения владельца остаётся ограниченным implicit-RUB контуром.
- MVP6: дополнительная provider family, enterprise storage, corporate/local AI
  и provider-independent acceptance; остальные adapter/policy контракты частичны.
- MVP7: weekly/event automation и citation-scoped Information Center (M7-09),
  versioned template approval (M7-03).
- MVP1: native exports, exact live mutation preconditions, 10k UI и полный XLSX/
  vision contracts остаются частичными, хотя базовый document flow существует.

### После реализации

Нужны same-SHA live-provider evidence, canary, затем отдельное разрешение на
production deploy. До этого ответ «можно полноценно работать» означает только
ограниченный тестовый пилот, не промышленную эксплуатацию полного ТЗ.

## Воспроизведение

```powershell
D:\PU-Workspace\.venv-pu-workspace-tests\Scripts\python.exe scripts\ci\v7_mvp_completion_matrix.py
D:\PU-Workspace\.venv-pu-workspace-tests\Scripts\python.exe -m pytest scripts\ci\test_v7_mvp_completion_matrix.py -q
```

Аудит не выполнял provider calls, миграции, push, merge, deploy или операции с
production. Статусы основаны только на файлах, доступных в проверенном commit.
