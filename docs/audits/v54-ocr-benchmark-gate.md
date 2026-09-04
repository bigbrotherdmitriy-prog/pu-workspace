# PU Workspace v5.4 — GATE-05 OCR benchmark

Дата проверки: 04.09.2026

База: `8ccc194bc834328e51a73225981f74d81775789a`

Ветка: `codex/v54-ocr-benchmark-gate`

## Решение

**PASS в границах owner-independent GATE-05:** локальный Tesseract реально обработал синтетический корпус, порог технической успешности выполнен, реквизиты имеют page+bbox, а низкая уверенность fail-closed отправляется человеку и не допускается к юридическим/финансовым действиям.

PASS не означает доказанную точность на клиентских или всех промышленных сканах. Ни один клиентский документ не использовался.

## Аудит исходного состояния

Существующий `backend/app/organizer_engine/content.py` уже содержал:

- локальный Tesseract `rus+eng`, PDF extraction и гибридный OCR слабых страниц;
- EXIF orientation, OSD autorotate, grayscale, autocontrast, median denoise и bounded deskew;
- Tesseract TSV, confidence токенов, bbox и постраничный результат;
- evidence номера, даты, сторон и суммы с номером страницы и координатами;
- заготовку табличных ячеек с row/column/bbox;
- сохранение OCR metadata и очередь ручной проверки;
- durable OCR batch с progress/cancel через существующий `BackgroundJob`;
- запрет задач, ответов, рисков и решений для `needs_review`.

Существующий benchmark в `test_ocr_commercial_hardening.py` проверял извлечение из заранее подготовленного текста. Он не запускал OCR и поэтому не мог подтвердить Tesseract, preprocessing или время страницы.

Найден дефект fail-closed: пустой результат попытки OCR изображения или слабой PDF-страницы имел confidence `0`, но `needs_review=false`, поскольку флаг зависел от наличия распознанного текста. Добавлен regression; теперь сама попытка OCR при уверенности ниже порога требует человека, включая пустой/нечитаемый результат.

## Реализованный benchmark

- 20 синтетических страниц, сгруппированных в 5 многостраничных документов;
- договор, счёт, акт, ГПР/контракт и ДДС/договор;
- детерминированные варианты `clean`, `low_contrast`, `noise`, `skew`;
- генерация PNG во временном каталоге, оригиналы отсутствуют и ничего не перезаписывается;
- обязательный локальный Tesseract, без fallback на внешний AI;
- до/после preprocessing: character accuracy и confidence;
- precision/recall/F1 номера, даты, сторон и суммы;
- техническая успешность, recognition rate, mean/p95 ms/page;
- manual-review rate и проверка fail-closed policy;
- покрытие evidence координатами и страницей.

Corpus SHA-256: `8586300F17636FC341C269E6C860D811DF223B5EEAB9B6924EF32CB8F5B022F0`.

## Фактические метрики

Среда: Windows, Tesseract `5.5.3.20260724`, языки `rus+eng`, Arial, внешний vision выключен.

| Метрика | Результат |
|---|---:|
| Страницы без технического сбоя | 20/20 (100%) |
| Страницы с распознанным текстом | 20/20 (100%) |
| Среднее время production extraction | 2601.878 ms/page |
| P95 production extraction | 4364.261 ms/page |
| Character accuracy до preprocessing | 99.89% |
| Character accuracy после preprocessing | 100.00% |
| Mean confidence до preprocessing | 95.59% |
| Mean confidence после preprocessing | 95.76% |
| Manual-review rate корпуса | 0/20 (0%) |
| Нарушения low-confidence policy | 0 |
| Evidence page+bbox coverage | 100/100 (100%) |

| Поле | Precision | Recall | F1 |
|---|---:|---:|---:|
| Номер | 100% | 100% | 100% |
| Дата | 100% | 100% | 100% |
| Стороны | 100% | 100% | 100% |
| Сумма | 100% | 100% | 100% |

Полный машинный протокол: `docs/audits/v54-ocr-benchmark-metrics.json`.

Нулевая доля manual review отражает качество данного безопасного синтетического корпуса. Негативный regression отдельно доказывает, что confidence ниже `0.72`, отсутствие движка или пустой OCR переводят документ в ручную проверку.

## Безопасность и архитектурные границы

- Базовый режим — только локальный Tesseract; содержимое наружу не передавалось.
- Внешний vision не вызывался и не имеет неявного fallback.
- Единственная разрешённая будущая граница внешнего vision — существующий `AIProviderAdapter`; default должен оставаться off.
- Benchmark не меняет jobs, Gmail, Google Drive, Яндекс Диск, production или базу данных.
- В JSON-метриках нет текста документов, локальных путей, DSN, токенов и provider identifiers.
- Для явного пути к локальному движку добавлен `TESSERACT_CMD`; значение пути не сохраняется в метриках или логах.

## Воспроизведение

Из каталога `backend`, при доступных Tesseract `rus+eng` и локальном кириллическом TTF:

```powershell
$env:PYTHONPATH='.'
$env:TESSERACT_CMD='C:\path\to\tesseract.exe'
python -m app.ocr_quality.benchmark `
  tests/fixtures/ocr_benchmark/corpus.json `
  --output ../docs/audits/v54-ocr-benchmark-metrics.json
python -m pytest tests/test_ocr_commercial_hardening.py tests/test_ocr_batch.py tests/test_v54_ocr_benchmark.py -q
```

Linux/контейнер использует `tesseract` из `PATH`; backend image уже устанавливает `tesseract-ocr`, `tesseract-ocr-rus`, `tesseract-ocr-eng` и `tesseract-ocr-osd`.

## Проверки

- exact local Tesseract benchmark test: `1 passed in 104.55s`;
- OCR + batch + benchmark unit/regression без повторного exact case: `19 passed, 1 deselected`;
- полный backend без повторного exact case: `1138 passed, 18 skipped, 1 deselected`;
- суммарно с отдельно выполненным exact case: `1139 passed, 18 skipped`;
- `git diff --check`: PASS;
- Python compilation: PASS.

18 пропусков полного backend относятся к ранее условным PostgreSQL/платформенным сценариям и не требуются для filesystem-only synthetic OCR gate. Они не заменены статическими проверками.

## Ограничения

- Синтетические изображения легче реальных фотографий, рукописей, печатей, сложных таблиц и чертежей.
- Corpus не калибрует confidence на размеченной производственной выборке; порог `0.72` остаётся продуктовой настройкой.
- OSD/autorotate и multi-page PDF уже покрыты функциональными тестами, но этот измерительный corpus использует PNG-страницы и bounded skew до 2 градусов.
- Табличная основа имеет координаты, но precision/recall сложных объединённых ячеек не является частью этого gate.
- Внешний vision намеренно не проверялся и не включался.

## Изменения схемы/интеграции

Новых моделей и миграций нет. Alembic head не меняется. Production activation отсутствует.
