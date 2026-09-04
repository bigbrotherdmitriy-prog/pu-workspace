# PU Workspace — результат P1 OCR hardening

Дата: 01.09.2026

Ветка: `codex/commercial-p1-ocr`

Исходный SHA: `ee2166ec5fca071d21d80d58e6a13507e7d4a773`

## Исходное состояние

До изменений единый модуль `backend/app/organizer_engine/content.py` поддерживал:

- нативный текст: DOCX, XLSX, XLS, TXT, CSV, Markdown и LOG;
- PDF с текстовым слоем;
- локальный Tesseract `rus+eng` для PNG, JPG/JPEG, TIFF, BMP, WEBP и сканированных PDF;
- гибридный PDF: OCR только страниц со слабым текстовым слоем;
- документные метрики `method`, `quality`, `total_pages`, `ocr_pages`, `warnings`;
- массовую переобработку через durable job `documents.ocr`.

Пробелы: не было preprocessing, постраничного confidence, областей доказательств, структурных реквизитов, координат таблиц, ручной OCR-проверки и наблюдаемого progress/cancel.

## Реализовано

1. Локальный preprocessing рабочей копии изображения:
   - EXIF/autoorientation;
   - grayscale;
   - autocontrast;
   - median denoise;
   - bounded deskew по горизонтальной проекции;
   - оригинал не перезаписывается.
2. Tesseract TSV вместо недоказуемой строки:
   - confidence каждого токена;
   - bounding box;
   - строка/блок;
   - итоговый confidence страницы.
3. Постраничный результат для нативного, OCR и гибридного PDF.
4. Извлечение реквизитов с evidence:
   - номер;
   - дата;
   - стороны;
   - сумма;
   - страница, excerpt, confidence и bbox, когда он доступен.
5. Основа таблиц: строки, столбцы, текст, confidence и координаты ячеек на базе Tesseract TSV.
6. Хранение в Document:
   - `ocr_confidence`;
   - `ocr_review_status`;
   - `ocr_metadata` JSON.
7. Очередь ручной проверки и manager-only подтверждение/отклонение.
8. Safety gate: low-confidence OCR индексируется, но не создаёт задачи, черновики ответов, риски или решения.
9. Durable OCR job публикует прогресс и поддерживает кооперативную отмену, используя существующий `BackgroundJob`, без изменения очереди.
10. Внешний vision не вызывается; Tesseract RU+EN остаётся базовым режимом.

## Benchmark

Корпус: 20 синтетических обезличенных страниц договоров, счетов и актов.

| Поле | Precision | Recall |
|---|---:|---:|
| Номер | 1.00 | 1.00 |
| Дата | 1.00 | 1.00 |
| Стороны | 1.00 | 1.00 |
| Сумма | 1.00 | 1.00 |

Техническая успешность: 20/20 страниц, 100%.

Ограничение benchmark: он измеряет детерминированное извлечение реквизитов из безопасного OCR-текста. Он не доказывает 100% качество Tesseract на реальных сканах. Для production-порогов нужен отдельный обезличенный корпус реальных сканов с ручной разметкой.

## Тесты

- Целевой OCR-набор: `20 passed`.
- Полный backend regression без integration: `354 passed in 5.75s`.
- PostgreSQL integration: `1 skipped`, потому что отсутствуют `PU_TEST_POSTGRES=1` и тестовая `DATABASE_URL` с именем БД, оканчивающимся на `_test`.
- Alembic: единственный head `b72c9f13a401`.

## Миграция

Добавлена миграция `b72c9f13a401_add_ocr_evidence_and_review.py`:

- `documents.ocr_confidence FLOAT NULL`;
- `documents.ocr_review_status VARCHAR(30) NOT NULL DEFAULT 'not_required'`;
- `documents.ocr_metadata JSON NULL`;
- индекс `ix_documents_ocr_review_status`.

## Известные ограничения

- Кооперативная отмена проверяется между документами; уже запущенный Tesseract для одной страницы завершается по timeout.
- Табличный слой является основой: объединённые ячейки, многострочные заголовки и сложные чертежи требуют последующей layout-модели.
- Bbox доступен для OCR-токенов; у нативного текста PDF/DOCX координаты пока отсутствуют.
- OSD зависит от установленного Tesseract language pack `osd`; при отсутствии ориентация остаётся неизменной.
- По умолчанию OCR ограничен 20 страницами и 25 МБ на файл; пределы управляются окружением.
- Зашифрованные и повреждённые PDF попадают в предупреждение/ручную проверку.
- Реальный PostgreSQL и реальные шумные сканы в текущем локальном окружении не проверялись.
