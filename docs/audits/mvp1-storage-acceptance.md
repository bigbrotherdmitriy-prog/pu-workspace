# MVP1 — synthetic storage acceptance

Дата проверки: 2026-09-05

Ветка: `codex/mvp1-storage-acceptance`

База: `6ceb635190e578fdbc596f07b00a25feb8be0122`

## Область и ограничения

Проверка выполнена только на синтетических Google Drive / Яндекс Диск
адаптерах. Реальные OAuth, токены, аккаунты, документы и provider API не
использовались. `App.tsx`, миграции, schema pins, production и секреты не
изменялись. Оригиналы и пользовательские данные не копировались и не удалялись.

## Найденный дефект и исправление

До исправления повторный `POST .../snapshot-queue` всегда возвращал последний
готовый snapshot. После появления или изменения объекта на диске повторное
сканирование было невозможно: новый объект оставался вне виртуального дерева и
анализа.

Добавлен явный параметр `refresh=true` к существующему endpoint. Контракт:

- обычный повтор POST остаётся идемпотентным и возвращает прежний snapshot;
- refresh готового snapshot создаёт новую неизменяемую версию;
- refresh при queued/running/retrying работе возвращает активный snapshot/job и
  не создаёт конкурирующий дубль;
- новый snapshot заново читает provider metadata, включая имя, checksum, размер
  и modified time;
- старый snapshot остаётся неизменным;
- exact binding `project_id/provider/connection_id/connection_row_id/folder_id`
  переносится в новый snapshot и проверяется worker-ом fail-closed.

Regression сначала воспроизводился на обоих провайдерах: ожидался новый
snapshot, фактически возвращался старый `id=1`. После исправления тест проходит.

## Acceptance matrix

| Сценарий | Google | Яндекс | Результат |
|---|---:|---:|---|
| Выбор папки любой глубины | PASS | PASS | stable locator, breadcrumbs и URL encoding |
| Exact project/provider/connection/folder binding | PASS | PASS | сохранён в SourceFolder, DriveConnection, snapshot и job |
| Persistent Project рядом с новым проектом | PASS | PASS | запросы, worker и результат остаются в новом проекте |
| Повтор HTTP-запроса | PASS | PASS | один snapshot/job |
| Явное обновление после завершения | PASS | PASS | новая версия snapshot |
| Добавленный объект | PASS | PASS | появляется только в новой версии |
| Изменённые checksum/size/modified time | PASS | PASS | новая версия обновлена, предыдущая неизменна |
| Анализ через durable job | PASS | PASS | status/result и measured progress=100 |
| Смена provider/connection до worker | PASS | PASS | 409 до чтения provider |
| Доступ пользователя другого проекта | PASS | PASS | 403/404, без provider access |
| Ошибка provider | PASS | PASS | безопасная ошибка без исходного текста |

## Performance contract

Корпус для каждого провайдера: 256 вложенных папок и 2 048 файлов, всего 2 304
объекта. Проверяется не нестабильный wall-clock, а стоимость provider I/O:

- ровно 257 вызовов `list_children` (корень + одна операция на папку);
- ни одного listing на файл;
- каждый object id встречается ровно один раз;
- hard limit останавливает дерево после превышения допустимого числа объектов;
- алгоритм использует очередь, поэтому глубина дерева не расходует стек Python.

Таким образом, число provider listing calls равно `F + 1`, а обход не содержит
квадратичного повторного сканирования дерева. Реальную сетевую latency и rate
limits этот синтетический контракт не подтверждает.

## Команды проверки

Из каталога `backend` с доступным Python окружением:

```powershell
python -m pytest -q tests/test_storage_binding_validation.py `
  tests/test_mvp1_storage_performance_contract.py `
  --basetemp=.pytest-mvp1-storage

python -m pytest -q tests/test_storage_provider_regression.py `
  tests/test_storage_adapter_contract_matrix.py `
  tests/test_yandex_storage_adapter_contract.py `
  --basetemp=.pytest-mvp1-adapters

git diff --check
```

## Остаточные ограничения

- UI пока не передаёт `refresh=true`: подключение кнопки обновления относится к
  отдельному frontend integration потоку, поскольку `App.tsx` запрещён данной
  задачей.
- Не проверены живые Google Drive / Яндекс Диск, OAuth rotation, сетевые retry и
  provider rate limits.
- PostgreSQL-конкурентность project row lock не запускалась в этой локальной
  acceptance; она должна войти в объединённый runtime CI.
- Синтетический progress подтверждает сохранённый worker progress/result, но не
  производительность распознавания реальных документов.

Итог локальной части MVP1: **PASS**. Live-provider и PostgreSQL runtime:
**CONDITIONAL** до интеграционного CI.
