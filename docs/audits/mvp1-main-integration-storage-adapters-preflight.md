# MVP-1 Main Integration — Storage Adapters Preflight

Дата: 2026-09-11

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед Storage adapters: `5b08d6699049556a3177577a1e004a0bd3fbfada` (OCR/XLSX-merge, закрыта и запушена).

Этот документ фиксирует результат read-only анализа слоя storage adapters перед
selective port из `codex/mvp1-phase2-review` (HEAD `4cd0a62`) в `main`, по той
же схеме, что и
[`mvp1-main-integration-snapshots-preflight.md`](mvp1-main-integration-snapshots-preflight.md)
и [`mvp1-main-integration-ocr-preflight.md`](mvp1-main-integration-ocr-preflight.md).
Он является планом следующей сессии. **Код не менялся, коммит не делался.**

Explicitly проверенные файлы (по заданию): `backend/app/integrations/storage.py`,
`backend/app/integrations/google_workspace.py`,
`backend/app/integrations/yandex_disk.py`, `backend/app/organizer_engine/drive.py`.
Часть `drive.py` уже была перенесена Snapshots-портом (commit `7db7f3d`) —
это **не дублируется** ниже; см. п. 1.1.

## 1. Метод и что уже закрыто раньше

### 1.1. Что Snapshots (7db7f3d) уже перенёс в этой области

Из [`mvp1-main-integration-snapshots-completion.md`](mvp1-main-integration-snapshots-completion.md)
п. 2 и фактического diff-stat коммита `7db7f3d`:

- `organizer_engine/drive.py`: bounded page size, `includeItemsFromAllDrives`/
  `supportsAllDrives=True` **на read-путях** (`get_file_meta`, `list_children`),
  retry на 429/403 rate-limit reasons и 5xx (`_execute_read`/`READ_RETRY_DELAYS`),
  shortcut-metadata (`shortcut_target_*`) без перехода к target.
- `integrations/yandex_disk.py`: только флаг
  `supports_exact_mutation_preconditions=False` перенесён точечно (по прямому
  решению пользователя); полная metadata-паритет с Google — отложена.
- Явно НЕ перенесено (зафиксировано как сознательное отклонение):
  `storage.py`/`google_workspace.py`/`catalog.py` (review — устаревший шум,
  откатывает более новую multi-scope OAuth-логику main), OCR-слой drive.py
  (`read_native_export_exact` и т.д.), вся подсистема
  `supports_exact_mutation_preconditions`/`exact_mutation_blocker` и
  `google_storage_mutation.py`/`storage_mutation_live.py`.

Этот preflight проверяет данные утверждения заново на текущем diff (main
успел уйти вперёд ещё на OCR-коммит после Snapshots) и разворачивает то, что
Snapshots сознательно отложил.

### 1.2. Масштаб diff на именно этих 4 файлах

```text
 backend/app/integrations/google_workspace.py |  10 +-
 backend/app/integrations/storage.py          |  38 ++----
 backend/app/integrations/yandex_disk.py      |   7 ++
 backend/app/organizer_engine/drive.py        | 171 ++++++++++++++++++++++++---
 4 files changed, 174 insertions(+), 52 deletions(-)
```

## 2. Архитектурные развилки

### 2.1. `integrations/storage.py` — чистый шум форматирования, НЕ развилка

Полный diff — построчный reflow (`black`/ручное сжатие многострочных вызовов
в main в одну строку), **без единого семантического отличия**: тот же порядок
проверок `google-credential:`/`google-token:`/legacy-fallback, те же
HTTPException-коды и тексты. Подтверждает характеристику Snapshots-документа
("устаревший шум"), но с уточнением: в самом `storage.py` шум чисто
косметический — реальная деградация логики (см. 2.2) находится в
`google_workspace.py`, не здесь.

**Решение: ничего не переносить.**

### 2.2. `integrations/google_workspace.py` — main строго впереди, НЕ развилка

```python
# main (текущий HEAD)
def configured(self) -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET")
                and os.getenv("GOOGLE_REDIRECT_URI"))

def health(self) -> AdapterHealth:
    token = self.db.scalar(select(GoogleOAuthToken).where(self._token_query()))
    ready = token is not None and bool(token.access_token) and self.configured()

# review (origin/codex/mvp1-phase2-review)
def configured(self) -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))

def health(self) -> AdapterHealth:
    token = self.db.scalar(select(GoogleOAuthToken.id).where(self._token_query()))
    ready = token is not None and self.configured()
```

Review убирает проверку `GOOGLE_REDIRECT_URI` из `configured()` и проверку
`bool(token.access_token)` из `health()` — адаптер считался бы "ready" по
пустой/недозаполненной строке токена. Проверено `git blame` + ancestry:

- `1aeb5e5` ("test: harden Google Workspace OAuth acceptance", 2026-09-04) —
  добавил `GOOGLE_REDIRECT_URI` в `configured()`.
- `55fa3e4` ("feat: integrate MVP5 trust runtime with production",
  2026-09-04) — добавил `bool(token.access_token)` в `health()`.
- Оба коммита **не являются предками** `origin/codex/mvp1-phase2-review`
  (`git merge-base --is-ancestor` → false), при том что HEAD review-ветки
  датирован позже (`2026-09-10`) — то есть review не "не успел" их получить
  синхронизацией, а является параллельной веткой, разошедшейся раньше и
  самостоятельно менявшей эти же строки в другую сторону. Это не гонка
  свежести, а содержательный откат двух independent hardening-коммитов main.

**Решение: не переносить. Regression-guard: `test_mvp1_google_storage_oauth.py`
(за вычетом уже известного pre-existing routing-дефекта, см.
[snapshots-completion](mvp1-main-integration-snapshots-completion.md) п.4 и
[ocr-preflight](mvp1-main-integration-ocr-preflight.md) п.11.2) должен
продолжать требовать `GOOGLE_REDIRECT_URI` и непустой `access_token`.**

Смежно (не в explicit-scope этого preflight, но тот же паттерн, стоит
зафиксировать raz): `integrations/catalog.py` — review схлопывает
`GOOGLE_CAPABILITIES` из `frozenset({scope, ...})` в одиночный `scope` и для
Gmail (`channel`) теряет требование `gmail.send`-scope (`connected`/`action`
перестают проверять его, только `gmail.readonly`). Это тот же откат, что и
Snapshots-документ уже фиксировал; подтверждено заново на текущем HEAD.
**Тоже не переносить**, но это отдельный файл — если Storage adapters решит
затронуть `catalog.py`, это нужно explicit-решением, не тихим побочным
эффектом.

### 2.3. `integrations/yandex_disk.py` — реальная, низкорисковая аддитивная развилка

```diff
 return StorageObject(
     id=path, name=..., mime_type=..., parent_id=parent_locator,
     md5_checksum=meta.get("md5"), size=meta.get("size"),
     modified_time=meta.get("modified"),
     object_type="folder" if meta.get("type") == "dir" else "file",
     provider="yandex_disk",
+    parent_ids=(parent_locator,) if parent_locator else (),
+    provider_revision=meta.get("revision") or meta.get("sha256") or meta.get("md5"),
+    web_url=meta.get("public_url") or meta.get("preview"),
+    source_path=path or None,
+    availability="available",
+    acl_state="unknown",
+    provider_metadata={"resource_id": meta.get("resource_id")},
 )
```

`StorageObject` (`backend/app/core/integration_types.py`) уже объявляет ВСЕ
эти поля на main — они часть Snapshots' metadata-envelope контракта
(`parent_ids`, `provider_revision`, `web_url`, `source_path`, `availability`,
`acl_state`, `provider_metadata`). Google's `DriveClient._to_file` их уже
заполняет (post-Snapshots); Yandex — нет, падает на дефолты
(`availability="unknown"`, `acl_state="unknown"`, `parent_ids=()`,
`provider_metadata=None`). Review закрывает именно этот паритет-гап, ровно
тот, что Snapshots-документ назвал "отложен на отдельную итерацию".

Проверено: место вставки (`_to_object`, `yandex_disk.py:77-91`) — чистая
add-on точка, все использованные переменные (`path`, `meta`, `parent_locator`)
уже в scope, drop-in без реструктуризации.

Два места, требующие explicit-решения при реализации (не решаю здесь):

1. `web_url=meta.get("public_url") or meta.get("preview")` — `preview` в
   Yandex Disk API это URL превью-миниатюры, а не "страница для просмотра
   файла" (семантически отличается от Google's `webViewLink`). Нужно
   проверить, является ли фоллбэк на `preview` корректным по факту, или
   должен остаться `None`, если `public_url` не задан.
2. `acl_state="unknown"` — жёстко захардкожен (не вычисляется), в отличие от
   Google, где `acl_state` реально выводится из `capabilities.canEdit`/
   `canDownload`. Нужно проверить, отдаёт ли Yandex Disk API вообще
   permission-поля в metadata resource; если нет — `"unknown"` честный
   дефолт, и это не баг, а предел API, зафиксировать явно в комментарии.

**Alembic-implications: нет.** `StorageObject`-поля уже существуют в схеме
(колонки `virtual_nodes`/`document`-metadata уже добавлены Snapshots'
миграциями); заполнение для Yandex — чисто runtime, без новых колонок.

**Решение: хороший кандидат на прямой порт с точечной адаптацией (проверить
два пункта выше), низкий риск, самодостаточен.**

### 2.4. `organizer_engine/drive.py` — пять разных, перепутанных в одном diff'е тем

Единый diff файла смешивает минимум 5 независимых concerns. Разбираю по
отдельности — **не единое решение "portировать/не портировать весь файл"**.

#### 2.4.a OCR vision-routing / native export — ВНЕ SCOPE (уже задокументировано)

`AIProviderAdapter`, `route_extraction`/`ExtractionPolicy`/`Mode` (из
`app.ocr_quality.routing`, которого нет на main), `GOOGLE_NATIVE_EXPORTS`,
`read_native_export_exact`, `NativeExportBytes`, `supports_exact_native_export`,
конструктор `DriveClient.__init__` с `extraction_mode`/`extraction_policy`/
`ai_adapter`, `populate_content` → `route_extraction(...)`.

Это ровно то, что [`ocr-preflight`](mvp1-main-integration-ocr-preflight.md)
п. 8/9 назвал будущим (не начатым, `routing.py` не портирован) и явно
исключил из уже закрытого OCR-коммита (`5b08d66`). **Не относится к Storage
adapters** — трогать нельзя без отдельного решения по OCR vision-routing.

#### 2.4.b `GOOGLE_EXPORTS` (legacy-словарь) — cross-area зависимость, НЕ тривиальный порт

```diff
 GOOGLE_EXPORTS = {
-    "application/vnd.google-apps.document": "text/plain",
-    "application/vnd.google-apps.spreadsheet": "text/csv",
-    "application/vnd.google-apps.presentation": "text/plain",
+    "application/vnd.google-apps.document": "application/pdf",
+    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
+    "application/vnd.google-apps.presentation": "application/pdf",
 }
```

Review's комментарий над этим блоком ("Text-oriented defaults used only by
the legacy analyzer") **не соответствует фактическому diff** — значения
меняются на `application/pdf`/`.xlsx`, не остаются текстовыми. Похоже на
устаревший/скопированный комментарий, не описывающий реальное изменение.

`GOOGLE_EXPORTS` **живой** на main: используется и в `populate_content`
(строка 172), и в `read_bytes` (строка 203) — оба метода в проде. Если
перенести это изменение, Google Docs/Slides начнут экспортироваться как PDF
(маршрут через `content.py`'s PDF/pypdf/OCR-пайплайн), а Google Sheets — как
настоящий `.xlsx` (маршрут через `_xlsx_text`/`xlsx_cells.py`, только что
захардened и расширенный в OCR-коммите `5b08d66`), вместо сегодняшнего
плоского `text/plain`/`text/csv`. Это прямо задевает область, которую OCR-
preflight уже закрыл без этого сценария в матрице проверок — новый test-case
на "экспорт Google Sheets как .xlsx проходит через `_xlsx_text`
security-bounds" не существует ни на одной ветке.

**Решение: не порт по умолчанию. Explicit-решение нужно ДО кода: либо (а)
не трогать `GOOGLE_EXPORTS` в этой области и оставить cross-area таск как
отдельный, либо (б) перенести и добавить новый regression-тест,
подтверждающий взаимодействие с уже закрытым OCR-контрактом.**

#### 2.4.c `supportsAllDrives=True` на мутирующих вызовах — реальный, низкорисковый гап

Snapshots уже включил `supportsAllDrives=True`/`includeItemsFromAllDrives=True`
на READ-путях (`get_file_meta`, `list_children`, строки 131/149-150). Review
добавляет тот же флаг на MUTATION-вызовах, которых Snapshots не касался:
`create_folder` (files().create), `copy_file` (files().copy),
`trash_safe_copy` (files().update trashed=true).

Это значит: сегодня на main пользователь может **прочитать/снапшотнуть**
папку из Shared Drive (спасибо Snapshots), но Safe Copy (`copy_folder_tree`
→ `create_folder`/`copy_file`) при попытке материализовать копию **внутри
той же Shared Drive** может упасть или повести себя не так, как ожидается —
Google Drive API требует `supportsAllDrives=true` на write-вызовах отдельно
от read. Ни одного теста на этот сценарий нет ни на одной ветке (не нашёл
`sharedDrive`/`teamDrive` upsert/copy-теста).

**Решение: хороший, самодостаточный кандидат на прямой порт. Нужен новый
тест** (ни на main, ни на review нет прямого теста именно на этот сценарий
для мутаций — review's тесты про Shared Drive это read-only pagination-тесты
из Snapshots-области).

#### 2.4.d Managed-copy ownership/idempotency — реальный фикс живого бага, но НЕ самодостаточен в границах 4 файлов

**Конкретно найденная проблема на main (не гипотетическая):**

`DriveClient.copy_folder_tree(..., idempotency_key=None)` — параметр
существует и корректно работает (подтверждено существующим main-тестом
`test_drive_safety.py::test_repeated_safe_copy_with_idempotency_key_does_not_duplicate_root`),
**но единственный реальный вызывающий код** — `organizer.py:138`
(`_scan_worker`) — **вызывает `copy_folder_tree` вообще без
`idempotency_key`**:

```python
copy_result = drive.copy_folder_tree(
    source_folder_id, source.parent_id, source.name, source_items=source_items,
)
```

Без `idempotency_key`, fallback — `datetime.now(timezone.utc)`-таймстамп в
имени папки-копии (`ts = idempotency_key or datetime.now(...)`). Крэш-
восстановление уже существует и реально сработает: `recover_incomplete_scans()`
(вызывается на рестарте процесса) находит сессии с незавершённым сканом и
вызывает `submit_scan(...)` заново для того же `session_id`. Если процесс
упал **во время** `copy_folder_tree` (после `create_folder` корневой папки,
но до того как `repo.update_session(session_id, copy_folder_id=...)`
закоммитился — единственный момент, где `copy_folder_id` персистится), то
при повторном запуске `_scan_worker` видит `session["copy_folder_id"]` всё
ещё пустым → запускает `copy_folder_tree` заново **с нуля**, с новым
таймстампом → создаёт **вторую, дублирующую, orphaned** папку-копию в
Drive. Существующий комментарий в коде ("we intentionally do not
trash/delete the partial copy automatically: retaining evidence is safer")
подтверждает, что осиротевшая частичная копия действительно остаётся
непристроенной.

**Review закрывает именно это**, но решение размазано по трём файлам, из
которых только один — `drive.py` — в explicit-scope этого preflight:

1. `drive.py` (in scope): `create_folder`/`copy_file` получают
   `app_properties` (`puManagedCopyKey`/`puManagedSourceId` как Drive
   `appProperties` — устойчивый к переименованию маркер владения, в отличие
   от текущего сопоставления по `item.name`); `copy_folder_tree` при
   `managed=True` переиспользует уже созданные дочерние объекты по
   `puManagedSourceId` вместо публикации заново (реальная возобновляемость
   после краша); новый `trash_managed_copy(copy_root_id, ownership_key)` —
   безопасный trash только полностью "своего" поддерева (защита от
   удаления чужого объекта, если маркер не совпадает); capability-флаги
   `supports_managed_copy_idempotency`/`supports_managed_copy_cleanup`.
2. `organizer_engine/managed_copies.py` (**новый файл, вне explicit-scope**):
   `managed_copy_identity()`/`snapshot_copy_key()` — детерминированный ключ
   из `(project_id, provider, connection_id, connection_row_id, folder_id,
   source_revision=snapshot.id)`, плюс большая подсистема
   `run_managed_copy_cleanup` для массового trash всех managed-копий проекта
   при архивации (durable worker-fencing, audit-log receipts,
   idempotent-replay — см. п. 3 ниже, это отдельная фича, не просто "тот же
   ключ").
3. `organizer.py` (**вне explicit-scope**): `_scan_worker` получает новый
   параметр `managed_copy_key` и **capability-gate** — если адаптер не
   поддерживает `supports_managed_copy_idempotency`, работа падает с
   `managed_copy_reconciliation_unavailable` **до** любого read/copy-вызова
   (fail-closed, не тихий fallback на старое поведение).

**Портировать только `drive.py`-часть — не закроет баг**: без wiring в
`organizer.py` (передать стабильный `managed_copy_key` вместо `None`)
`idempotency_key`-параметр `copy_folder_tree` остаётся ровно так же мёртв,
как сегодня. Это нужно явно проговорить перед реализацией: либо Storage
adapters area расширяет explicit-scope на `organizer.py` (минимально —
threading одного параметра, без capability-gate и без
`managed_copies.py`'s cleanup-подсистемы), либо фиксируется как известный
gap с отдельным тикетом.

**Yandex не подвержен той же уязвимости** тем же образом: его собственный
`copy_folder_tree` (`integrations/yandex_disk.py:185`) строит имя
copy-root'а из детерминированного хэша source-пути
(`hashlib.sha256(source...)`) даже когда `idempotency_key` не передан (в
отличие от main's Drive-версии, которая в этом случае берёт
time-based-таймстамп) — а `create_folder`/copy-запросы уже сегодня трактуют
HTTP 409 ("уже существует") как успех. Path-based identity Yandex Disk
делает его copy естественно идемпотентным по построению; это asymmetрия
стоит знать при приоритизации (Google — реальный риск, Yandex — не в этом
конкретном сценарии).

**Решение: не тривиальный порт. Explicit-решение нужно до кода:
(а) минимальный фикс — threading стабильного `idempotency_key` из
`organizer.py` в существующий `copy_folder_tree` без всей
managed_copies.py-инфраструктуры (меньший diff, закрывает дубли, не даёт
resumability/cleanup); (б) полный перенос design'а из review (несёт
`managed_copies.py` целиком — но там ещё и project-archival cleanup
subsystem, см. п. 3, которая явно больше "storage adapters").**

#### 2.4.e `supports_exact_mutation_preconditions=False`/`exact_mutation_blocker` — низкая ценность в одиночку

Capability-флаг self-documenting ("Drive v3 files.update has no exact
revision precondition"), уже частично перенесён для Yandex в Snapshots.
Единственный потребитель — `google_storage_mutation.py`/
`storage_mutation_live.py`, которые **не существуют на main** (см. п. 3).
Портировать флаг на `DriveClient` без потребителя — безвредно, но
бесполезно (мёртвый код) до тех пор, пока exact-mutation subsystem не
решена отдельно.

**Решение: низкий приоритет; портировать вместе с п. 3, если/когда та
область будет решена, не раньше.**

## 3. Смежная, но НЕ включённая в explicit-scope область — обнаружена в процессе анализа

При поиске потребителей `supports_exact_mutation_preconditions` и
`idempotency_key`-wiring обнаружена **отдельная, крупная, полностью
непортированная подсистема**, которую пользователь не называл в списке из 4
файлов, но которая напрямую примыкает к Storage adapters и о которой стоит
знать до планирования следующих веток:

| Файл (review-only, отсутствует на main) | Размер |
|---|---|
| `app/integrations/google_storage_mutation.py` | 115 строк |
| `app/integrations/storage_mutation_live.py` | 294 строки |
| `app/api/storage_mutations.py` | — |
| `app/organizer_engine/storage_mutation_jobs.py` | — |
| `app/organizer_engine/storage_mutation_repository.py` | — |
| `app/organizer_engine/storage_mutation_runtime.py` | — |
| `app/organizer_engine/storage_mutations.py` | — |
| `app/organizer_engine/managed_copies.py` (project-archival cleanup) | ~250 строк |

Плюс минимум 13 тестовых файлов (`test_mvp1_storage_mutation_*.py` ×6,
`test_managed_copy_identity.py`, `test_mvp1_google_managed_copy.py`,
`test_mvp1_google_conditional_mutation.py`,
`test_mvp1_google_mutation_runtime_configuration.py`,
`test_mvp1_google_storage_live.py`, `test_mvp1_storage_live_adapters.py`,
`test_mvp1_storage_provider_primitives.py`).

Это exact-precondition mutation execution (rename/move с проверкой
etag/revision перед записью) **плюс** отдельная, полностью самостоятельная
фича — durable "trash all managed copies on project archive" с
worker-lease-fencing и audit-log-receipt-based idempotent replay
(`managed_copies.py::run_managed_copy_cleanup`, см. цитату в п. 2.4.d) —
трогает модели `AuditLog`, `BackgroundJob`, `OrganizerSession`, `Project`,
`ProjectMember`, `User`, `WorkspaceSnapshot`.

**Рекомендация: не сворачивать в Storage adapters.** Это по объёму и
сложности (durable fencing, отдельный API-роут, отдельная джоб-очередь)
сопоставимо с целой отдельной пройденной областью (Snapshots/OCR) — если
понадобится, заслуживает своего preflight-документа
(`mvp1-main-integration-storage-mutation-preflight.md` или похожий), а не
попутного пункта здесь. `google_retry.py` (review-only) сюда **не
относится** — используется только Gmail-интеграцией
(`api/gmail.py`/`gmail_history.py`/`staging/gmail.py`), ложный след,
исключён из анализа.

Единственное, что из этой большой подсистемы прямо примыкает к 4 explicit-
scope файлам — п. 2.4.d/2.4.e выше (capability-флаги на `DriveClient`,
которые эта подсистема потребляла бы, будь она портирована).

## 4. Обязательная матрица проверок

| Инвариант | Тест | Состояние |
|---|---|---|
| `storage.py` provider-роутинг (`google-credential:`/`google-token:`/legacy) не регрессирует | Нет прямого unit-теста ни на одной ветке — покрыто косвенно через `test_storage_provider_regression.py` и API-уровневые тесты | Regression-guard, уже PASS на main, не трогать |
| Google OAuth `health()` требует непустой `access_token` и `GOOGLE_REDIRECT_URI` | `test_mvp1_google_storage_oauth.py` (минус pre-existing routing-дефект, см. п.1) | Regression-guard, обязателен после любой правки `google_workspace.py` |
| Gmail-capability требует `gmail.send`, не только `gmail.readonly` | Нет прямого теста на этот конкретный сценарий ни на одной ветке (гап, не блокирует, но стоит закрыть при следующей правке `catalog.py`) | Пробел — не блокирует эту область (catalog.py вне explicit-scope) |
| Google/Yandex реализуют единый provider-neutral контракт | `test_storage_adapter_contract_matrix.py` (2 теста) | Regression-guard, уже PASS |
| Yandex adapter — provider-errors нормализованы, access_token не в ошибке | `test_yandex_storage_adapter_contract.py` (5 тестов) | Regression-guard, уже PASS |
| Yandex metadata envelope (`parent_ids`/`provider_revision`/`web_url`/`source_path`/`availability`/`acl_state`/`provider_metadata`) заполняется | Тест отсутствует на обеих ветках в этом виде — есть только на структуру `StorageObject` в Google-варианте (`test_mvp1_google_metadata_contract.py`, Snapshots-область) | Обязательный новый тест, если решение 2.3 — портировать |
| `walk_tree` — линейный provider-call бюджет и жёсткий item-limit, единообразно для обоих провайдеров | `test_mvp1_storage_performance_contract.py` (review, 2 параметризованных теста, provider-neutral, **не требует адаптации** — вызывает только `adapter.list_children`/`walk_tree`) | Хороший кандидат на прямой порт как regression-guard независимо от остальных решений |
| Safe copy внутри Shared Drive (`supportsAllDrives` на мутациях) не падает | Тест отсутствует на обеих ветках | Обязательный новый тест, если решение 2.4.c — портировать |
| Повторный `copy_folder_tree` с тем же `idempotency_key` не дублирует корень | `test_drive_safety.py::test_repeated_safe_copy_with_idempotency_key_does_not_duplicate_root` | Уже PASS на main (примитив корректен) — но не доказывает, что реальный caller передаёт ключ (см. 2.4.d) |
| Реальный caller (`_scan_worker`) передаёт стабильный `idempotency_key`/`managed_copy_key` в `copy_folder_tree` | Тест отсутствует на main; на review — `test_managed_copy_identity.py::test_copy_worker_does_not_activate_unproven_name_reuse` (требует `managed_copies.py` + `organizer.py`-wiring, вне explicit-scope как есть) | Обязательный пробел по существу (см. 2.4.d) — минимум нужен тест "crash mid-copy → retry не создаёт вторую папку", если выбран вариант (а) или (б) |
| Managed-copy retry переиспользует уже скопированные дочерние объекты, не дублирует | `test_mvp1_google_managed_copy.py::test_managed_copy_retry_reconciles_existing_children_without_duplicate` | Портируется только вместе с решением 2.4.d по варианту (б) |
| `trash_managed_copy` отклоняет чужой/неполный поддерево до trash | `test_mvp1_google_managed_copy.py::test_cleanup_rejects_foreign_descendant_before_trash`, `test_cleanup_accepts_exact_owned_subtree_and_is_idempotent_at_provider_boundary` | Портируется только вместе с решением 2.4.d по варианту (б) |
| Exact-precondition mutation (rename/move по etag) — fail-closed на missing/changed etag | `test_mvp1_google_conditional_mutation.py` (4 теста) | Вне explicit-scope (см. п. 3) — не требуется для этой области |
| Project-archival managed-copy cleanup — durable, idempotent replay | `test_mvp1_storage_mutation_*.py` ×6 | Вне explicit-scope (см. п. 3) — отдельная будущая область |
| GOOGLE_EXPORTS MIME-смена не ломает OCR-контракт (`_xlsx_text` security-bounds, pypdf-путь) | Тест отсутствует ни на одной ветке | Обязательное явное решение перед портом (см. 2.4.b) — не тривиальный regression-guard, новый сценарий |

## 5. Уровни доказательства

### Offline / synthetic

Все инварианты п. 4, за исключением managed-copy crash-recovery сценария
(нужен реальный retry/worker-restart симулятор, но это возможно чисто
offline — `recover_incomplete_scans()` не требует PostgreSQL-специфичного
поведения, только персистентность через SQLAlchemy ORM, доступную в SQLite)
и без реальных Google/Yandex API — все существующие и предполагаемые новые
тесты используют fake/synthetic `service`/`_Files`/HTTP-моки, как
`test_drive_safety.py`/`test_yandex_storage_adapter_contract.py` уже делают.

### Tested runtime / PostgreSQL

**Предварительно не требуется** для explicit-scope (2.1–2.4.c): чистая
runtime-логика адаптеров, никаких новых миграций, никакой DB-конкурентности.

**Может потребоваться**, если реализация выберет закрыть 2.4.d вариантом (б)
целиком (полный managed_copies.py) — `run_managed_copy_cleanup` использует
`SELECT ... FOR UPDATE`-подобный worker-fencing
(`execution_options(populate_existing=True)`, тот же паттерн, что вызвал
реальный race-баг в Snapshots, см.
[snapshots-completion](mvp1-main-integration-snapshots-completion.md) "Найденный
и исправленный реальный баг") — если это решение выбрано, нужен отдельный
PostgreSQL-конкурентный тест по аналогии, не решаю здесь.

## 6. Alembic

**Новых миграций не требуется** для explicit-scope 2.1–2.4.c: все нужные
поля (`StorageObject`/`VirtualNode` metadata envelope) уже в схеме после
Snapshots. Текущий head после OCR: `201286e2acd0` (без изменений, OCR-коммит
`5b08d66` тоже не добавлял миграций).

Если 2.4.d решится в пользу полного `managed_copies.py` (вариант б) — та
подсистема сама по себе не добавляет новых колонок (использует существующие
`AuditLog`/`BackgroundJob`/`OrganizerSession`/`WorkspaceSnapshot` как есть,
судя по импортам), но это нужно подтвердить отдельно при реализации, не
здесь.

## 7. Secrets scan

Код не менялся, коммита нет — scan не выполнялся. Требуется по полному diff
перед будущим storage-adapters-коммитом, тем же методом, что в Snapshots/OCR
(grep по паттернам ключей/токенов плюс проверка высокоэнтропийных строк).

## 8. Предполагаемый состав будущего коммита

Фактически изменено `0` файлов. Ожидаемый минимальный набор (уточнится по
факту реализации, особенно по итогам решений 2.4.b/2.4.d):

- `backend/app/integrations/yandex_disk.py` — точечно: metadata envelope
  паритет (2.3), с явным решением по `web_url`/`acl_state` (2.3 пп. 1-2).
- `backend/app/organizer_engine/drive.py` — точечно: `supportsAllDrives=True`
  на `create_folder`/`copy_file`/`trash_safe_copy` (2.4.c); managed-copy
  primitives (2.4.d), объём зависит от выбранного варианта (а)/(б).
- `backend/app/organizer_engine/managed_copies.py` — новый файл, **только
  если** выбран вариант (б) для 2.4.d; при варианте (а) — не создаётся,
  вместо этого одна точечная правка в `organizer.py`.
- `backend/app/organizer.py` — точечно: `_scan_worker` получает и передаёт
  стабильный idempotency/managed-copy ключ в `copy_folder_tree` (обязательно
  для 2.4.d в любом варианте — иначе фикс мёртв, см. 2.4.d).
- `backend/tests/test_mvp1_storage_performance_contract.py` — новый, порт
  без адаптации.
- Новый тест на Shared-Drive safe-copy (2.4.c) — с нуля, гап из п. 4.
- Новый тест на crash-mid-copy retry (2.4.d) — с нуля или адаптация
  `test_managed_copy_identity.py`/`test_mvp1_google_managed_copy.py` в
  зависимости от варианта.
- Новый тест на Yandex metadata envelope (2.3) — с нуля, по аналогии с
  `test_mvp1_google_metadata_contract.py`.
- Итоговый audit области (`mvp1-main-integration-storage-adapters-completion.md`).

**НЕ входит** (explicit exclusions, требуют отдельного решения/preflight):
`google_workspace.py`, `storage.py`, `catalog.py` (2.1/2.2 — main строго
впереди), весь OCR vision-routing слой `drive.py` (2.4.a), `GOOGLE_EXPORTS`
MIME-смена без cross-area решения (2.4.b, если не выбран явно), вся
mutation-execution/project-archival-cleanup подсистема (п. 3).

Это планируемый, а не финальный список.

## 9. Ограничения на реализацию

- `organizer_engine/drive.py` **не заменять** целиком — только точечные
  добавления из 2.4.c и (по решению) 2.4.d; весь Snapshots'-envelope/retry/
  pagination-механизм остаётся нетронутым.
- `integrations/google_workspace.py`, `integrations/storage.py`,
  `integrations/catalog.py` **не трогать вообще** со стороны review — main
  строго впереди (п. 2.1/2.2), review здесь откатывает hardening.
- `GOOGLE_EXPORTS` (2.4.b) — **не переносить молча**; если переносится,
  обязателен новый cross-area regression-тест против уже закрытого OCR-
  контракта (`5b08d66`), решение принять явно до кода.
- Managed-copy (2.4.d) — если выбран вариант (а) (минимальный threading
  ключа без `managed_copies.py`), **не создавать** `organizer_engine/
  managed_copies.py` попутно — это явный, отдельно обсуждаемый выбор
  масштаба, не default.
- Вся подсистема exact-precondition mutation execution и project-archival
  managed-copy cleanup (п. 3: `google_storage_mutation.py`,
  `storage_mutation_live.py`, `api/storage_mutations.py`,
  `organizer_engine/storage_mutation_*.py`) **не переносится** в рамках этой
  области — отдельная будущая веха, вне scope, зафиксировать это в итоговом
  audit, чтобы не создать иллюзию полного mutation-hardening.
- Решения по 2.4.b и 2.4.d (варианты) должны быть приняты явно **до**
  написания кода, не по ходу дела — как и решение по XLSX merge в OCR-
  области.
- Любое отклонение от этих ограничений — описать до коммита, как и в
  Snapshots/OCR.

## 10. Git-состояние на preflight

```text
git diff --check: PASS
git status --porcelain: clean
```

Код не менялся, коммит не делался. Следующая сессия должна начать с этого
документа, в первую очередь утвердить решения по 2.3 (два открытых пункта),
2.4.b (переносить ли `GOOGLE_EXPORTS`) и 2.4.d (вариант а/б), затем выполнить
selective port и пройти гейты: offline regression, PostgreSQL tested-runtime
(только если выбран 2.4.d вариант б), secrets scan, итоговый audit.

## 11. Резолюция 2.3/2.4.b/2.4.d — implementation log (эта сессия)

**Scope этой сессии — ровно три пункта, явно НЕ весь план п. 8.** 2.1/2.2
(main строго впереди, не трогать), 2.4.a (OCR vision-routing, вне scope),
2.4.c (`supportsAllDrives` на мутациях) и 2.4.e (`exact_mutation_blocker`)
**не решались в этой сессии** и остаются открытыми пунктами плана.

### 11.1. Принятые решения (зафиксированы до кода)

- **2.3 (Yandex metadata parity) — перенесено**, с двумя уточнениями:
  `web_url = meta.get("public_url")` без fallback на `preview`; `acl_state`
  остаётся `"unknown"` с комментарием в коде, поясняющим, что это честный
  потолок API, а не баг.
- **2.4.b (`GOOGLE_EXPORTS`) — НЕ перенесено.** Остаётся отдельным, явно
  отложенным cross-area таском (см. п. 11.4).
- **2.4.d (managed-copy) — минимальный фикс, вариант (а).** Explicit-scope
  расширен ровно на два файла: `organizer_engine/drive.py` (app_properties/
  `puManagedCopyKey`/`puManagedSourceId`, один capability-флаг) и
  `organizer.py` (одна строка — threading стабильного ключа в существующий
  вызов `copy_folder_tree`). `organizer_engine/managed_copies.py` и
  project-archival cleanup subsystem **не перенесены** — остаются
  зафиксированным gap'ом (см. п. 11.4).

### 11.2. Фактически изменённые/новые файлы

- `backend/app/integrations/yandex_disk.py` — точечно: `_to_object()`
  заполняет `parent_ids`/`provider_revision`/`web_url`/`source_path`/
  `availability`/`acl_state`/`provider_metadata`, оба уточнения из 11.1
  учтены буквально.
- `backend/app/organizer_engine/drive.py` — точечно: `create_folder`/
  `copy_file` получили `app_properties`; `copy_folder_tree` переписан с
  name-based на ownership-marker-based reconciliation (root по
  `puManagedCopyKey`, каждый child по `puManagedSourceId`, уже
  скопированные объекты переиспользуются, а не создаются заново); новый
  capability-флаг `supports_managed_copy_idempotency = True`.
  `supports_managed_copy_cleanup` **не добавлен** — `trash_managed_copy` не
  портирован (это была бы ложная capability-декларация). Adaptive-PSM/OCR-
  слой, read-путь (`get_file_meta`/`list_children`), retry-механизм не
  тронуты.
- `backend/app/organizer.py` — одна строка: `_scan_worker`'s единственный
  вызов `copy_folder_tree` получает
  `idempotency_key=f"organizer-session-{session_id}"`. Без
  capability-gate, без `managed_copy_key`-параметра в сигнатуре функции —
  ключ вычисляется inline из уже имеющегося `session_id`, как решено.
- `backend/tests/test_drive_safety.py` — фейковый `_Files` дополнен
  round-trip'ом `appProperties` через `create()` (был не нужен раньше, стал
  обязателен: старая логика сравнивала по имени, новая — по маркеру) и
  новым методом `copy()` (отсутствовал); добавлен новый тест
  `test_crash_mid_copy_retry_resumes_instead_of_duplicating_root_or_children`
  — прямая регрессия на баг из п. 2.4.d: сид симулирует частичную копию
  (root + один из двух файлов, оба с маркером), проверяет, что retry
  переиспользует root и уже скопированный файл, копирует только
  недостающий.
- `backend/tests/test_managed_copy_key_wiring.py` — новый, 2 теста:
  `_scan_worker` действительно передаёт `idempotency_key=
  "organizer-session-{id}"` в `copy_folder_tree` (без запуска остального
  пайплайна — фейковый `drive.copy_folder_tree` прерывает выполнение сразу
  после записи полученных kwargs, остальные коллабораторы
  (`index_documents`, `build_proposal`, Telegram и т.д.) не участвуют и не
  мокаются, т.к. не относятся к проверяемому факту); второй тест — что ключ
  идентичен между "первой попыткой" и "retry той же сессии" (ровно то
  свойство, которого не хватало на main).

### 11.3. Offline / synthetic regression — фактические числа

Тот же контейнер-образ (`app-backend:cf06cf21544a428ab13876f1273c7ca53128fe9d`),
эфемерный запуск, `--network none`, `DATABASE_URL=sqlite:///:memory:`.

- `test_drive_safety.py` (6, включая 1 новый), `test_managed_copy_key_wiring.py`
  (2, новый), `test_yandex_storage_adapter_contract.py` (5),
  `test_storage_adapter_contract_matrix.py` (2),
  `test_storage_provider_regression.py` (3), `test_job_hardening_contract.py`
  (3), `test_v54_staging_crypto.py` (13) — **PASS**, 25/25.
- `test_storage_binding_validation.py`, `test_parallel_validation_integration.py`,
  `test_mvp1_google_storage_oauth.py` вместе — 88 passed, 1 skipped
  (PostgreSQL-only), **1 failed** — тот же pre-existing routing-дефект
  (`test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision`),
  задокументированный в [snapshots-completion](mvp1-main-integration-snapshots-completion.md)
  п. 4 и подтверждённый повторно в [ocr-preflight](mvp1-main-integration-ocr-preflight.md)
  п. 11.2 — не новый, не связан с этой областью.
- Полный `backend/tests/`: **1543 passed** (было 1540 после OCR-коммита;
  +3 — ровно новые тесты этой сессии), 25 skipped, тот же 1
  pre-existing failure, 533.84s.

### 11.4. Explicit gap'ы, зафиксированные, а не решённые

- **2.4.b (`GOOGLE_EXPORTS`)**: изменение экспорт-формата Google Docs/Sheets
  (`text/plain`/`text/csv` → `application/pdf`/`.xlsx`) **отложено
  целиком**. Это отдельный, будущий cross-area таск: прежде чем его брать в
  работу, нужен новый regression-тест, подтверждающий совместимость с уже
  закрытым и запущенным OCR-контрактом (`5b08d66` — `_xlsx_text`
  security-bounds, `xlsx_cells.py` structural cells, pypdf/OCR-путь для PDF).
  Не Storage adapters по объёму работы, требуемой для безопасного переноса.
- **Managed-copy lifecycle (project-archival cleanup)**: `organizer_engine/
  managed_copies.py` (`managed_copy_identity`/`snapshot_copy_key`,
  `run_managed_copy_cleanup` с durable worker-fencing и audit-log-receipt
  idempotent replay) и вся смежная mutation-execution подсистема
  (`google_storage_mutation.py`, `storage_mutation_live.py`,
  `api/storage_mutations.py`, `organizer_engine/storage_mutation_*.py`, 13+
  тестовых файлов — см. п. 3 этого документа) **не перенесены**. Текущий
  минимальный фикс (11.1) закрывает конкретный найденный баг (дублирующая
  orphaned-копия при краше во время `copy_folder_tree`), но **не даёт**
  массовый managed-copy trash при архивации проекта — это отдельная будущая
  область, заслуживающая своего preflight, не попутного пункта здесь.
  `DriveClient.trash_managed_copy` и `supports_managed_copy_cleanup`
  сознательно не добавлены (см. 11.2), чтобы не декларировать
  несуществующую capability.
- **2.4.c (`supportsAllDrives` на мутациях) и 2.4.e (`exact_mutation_blocker`)**
  — не решались в этой сессии (пользователь запрашивал решение только по
  2.3/2.4.b/2.4.d). Остаются открытыми пунктами плана раздела 2 этого же
  документа.

### 11.5. PostgreSQL tested runtime

**Явно не требуется для реализованного минимального фикса (вариант а).**
Обоснование, как и запрошено — не просто "не требуется", а почему:

Пользователь отметил риск: "если threading `managed_copy_key` через
`organizer.py` трогает concurrency-путь при crash-recovery — нужен тест на
это, аналогично Snapshots' immutability/concurrency тестам". Проверено
явно:

1. `_scan_worker`'s единственное изменение — один добавленный kwarg в уже
   существующий вызов. Новых SQL-запросов, новых блокировок
   (`SELECT ... FOR UPDATE`), новых мест разыменования SQLAlchemy identity
   map — не добавлено. Именно такой паттерн (`populate_existing=True` на
   заблокированной строке) вызвал реальный race-баг в Snapshots
   (`_locked_snapshot`) — здесь его аналога нет.
2. Единственный реальный "конкурентный" сценарий для этого кода —
   crash-recovery retry: `recover_incomplete_scans()` находит sessions в
   незавершённом статусе и вызывает `submit_scan(...)` → `enqueue(...,
   idempotency_key=f"organizer.scan:{session_id}")`. `enqueue()` (см.
   `backend/app/jobs/queue.py`) дедуплицирует по этому ключу: если
   `BackgroundJob` с таким `idempotency_key` уже существует (в любом
   статусе, включая `"running"`), **новая job-строка не создаётся** —
   возвращается существующая. Это значит: сам job-queue уже гарантирует
   ровно один активный джоб на `session_id`; воркер получает новый claim на
   тот же джоб только после того, как `recover_expired()` (lease-based
   recovery, независимый существующий механизм, не тронут этой сессией)
   пометит его `"retrying"` из-за истёкшего lease — то есть строго
   **последовательно** (старый воркер лизу удержать уже не может), а не
   конкурентно.
3. Значит фактическая "гонка", которую нужно доказать тестом — не
   параллельный доступ к одной БД-строке (это уже покрыто существующим,
   не тронутым здесь job-queue lease-механизмом), а последовательный retry
   с сохранением идентичного ключа — что именно и проверяет
   `test_scan_worker_idempotency_key_is_stable_across_a_crash_recovery_retry`
   (offline, `test_managed_copy_key_wiring.py`) плюс
   `test_crash_mid_copy_retry_resumes_instead_of_duplicating_root_or_children`
   (offline, `test_drive_safety.py`) — вместе они проверяют именно ту цепочку,
   которая была реальным багом: стабильный ключ → повторный вызов →
   `copy_folder_tree` узнаёт свою прежнюю попытку по маркеру.

Ни новых Alembic-миграций, ни новых DB-колонок, ни новой
DB-конкурентности эта реализация не вводит.

### 11.6. Secrets scan

Выполнен по фактическому diff'у, тем же методом, что в Snapshots/OCR: grep
по паттернам ключей/токенов и по высокоэнтропийным base64-подобным строкам
≥32 символов. Все совпадения — имена env-переменных/полей внутри анализа
OAuth (`GOOGLE_CLIENT_SECRET`, `token.access_token` и т.п., это код и
комментарии о коде, не значения) и git-SHA. Реальных секретов и
высокоэнтропийных значений не найдено. **Чисто.**

### 11.7. Ограничения — соблюдены

`organizer_engine/drive.py` не заменён целиком (только точечные правки
из 11.1). `google_workspace.py`/`storage.py`/`catalog.py` не тронуты.
`GOOGLE_EXPORTS` не перенесён без cross-area решения. Managed-copy: выбран
вариант (а), `organizer_engine/managed_copies.py` не создан — explicit,
осознанный выбор объёма, не default. Вся mutation-execution/project-archival-
cleanup подсистема не перенесена, зафиксирована как отдельная будущая
область (11.4). Решения по 2.3/2.4.b/2.4.d приняты явно до кода.

Коммит по итогам п. 11 ещё не сделан — ждёт подтверждения пользователя.
