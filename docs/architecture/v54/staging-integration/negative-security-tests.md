# Негативные security-тесты

| ID | Сценарий | Обязательный результат |
|---|---|---|
| STG-N01 | `reference_only`, запрошен OCR | Provider body не читается; zero staging/temp/raster/text/job. |
| STG-N02 | Copy allow, derive policy absent | Encrypted stage может быть создан только для declared operation; OCR/index/task/draft/AI не запущены. |
| STG-N03 | Access/copy/retention policy pin absent/stale | Admission DENY; approval/admin role не bypass. |
| STG-N04 | Provider ETag/revision changed between decision and body | Abort; no evidence от old SourceVersion, no retry как same observation. |
| STG-N05 | Source or connection identity revoked before worker read | No decrypt/read/derive; cleanup scheduled; safe reason-only log. |
| STG-N06 | Revoke after decrypt before evidence commit | Commit only with explicit offline-processing grant; otherwise rollback metadata and purge manifest. |
| STG-N07 | Forged tenant/project/owner/request ID | Same deny response; no existence oracle and no storage read. |
| STG-N08 | Two workers claim/reclaim one request | One effective read/commit; same representation reused, no second ciphertext object. Stale fence cannot commit or delete active artifacts. |
| STG-N09 | Worker crashes while WRITING/PROCESSING | Age+fence recovery handles partial; active writer is not glob-deleted; lease recovery does not create a new copy. |
| STG-N10 | Cancel in QUEUED/PROCESSING | No new derives after cancellation fence; all manifest artifacts go PURGE_DUE; `expired` alone not success. |
| STG-N11 | Terminal failure / dead-letter | Policy-bounded recovery only; after deadline ciphertext, DEK, temp and derives are purged; no infinite failed retention. |
| STG-N12 | Ciphertext/header/chunk/checksum tampered | Auth/integrity failure, no plaintext/evidence; quarantine/purge and safe error class only. |
| STG-N13 | Wrong/missing/rotated KEK | `UNREADABLE`; no key fallback or plaintext restage; policy-authorized reacquisition only. |
| STG-N14 | Path traversal/symlink/hardlink/TOCTOU | No access outside configured private root; opaque key validation and no-follow semantics. |
| STG-N15 | Temp directory on normal disk/swap | Admission for no-copy/no-retain denied unless approved processing environment proves controls; no silent fallback from tmpfs/encrypted workspace. |
| STG-N16 | OCR creates source PDF, page JPG, processed PNG | Every artifact predeclared/tracked and removed on success/fail/cancel/crash; evidence points to raster representation and transform. |
| STG-N17 | Extracted text/quote/embedding disallowed | No DB/search/vector/cache/log content; other allowed outputs do not expand permission. |
| STG-N18 | Backup policy denies or unknown | Ciphertext, wrapped DEK, metadata hash and temp volume excluded; infrastructure unable to enforce causes admission DENY. |
| STG-N19 | Backup restored after revoke/purge | Reads remain blocked until tombstones/revocations/purge replay; restored bytes are purged/quarantined. |
| STG-N20 | Retention deletion fails repeatedly | `PURGE_FAILED`, alert/SLA/operator retry; never report PURGED or silently clear DEK metadata only. |
| STG-N21 | Snapshot completes under no-copy | Metadata snapshot may complete, but safe-copy job/session/provider write count stays zero. |
| STG-N22 | Staged/authorized copy used for extraction | Evidence records actual representation ID; it is not labelled provider original. |
| STG-N23 | Job payload/DB/log inspection | Only opaque request ID/correlation/job ID and safe enums; no owner/project/hash/size/MIME/name/path/URL/body/base64/token/text. |
| STG-N24 | Same bytes from two tenants/projects | No cross-scope dedup/object/key; opaque responses do not reveal equality. |
| STG-N25 | Cleanup one tenant while another writes | Manifest-scoped deletion only; no global wildcard affects another request. |
| STG-N26 | External AI adapter enabled, policy denies | Zero external requests and prompt fragments; local processing may continue only if separately allowed. |

## Runtime evidence

Для PASS нужны не тольо unit tests, но и PostgreSQL + shared staging
storage tests с двумя workers, kill/restart, concurrent cleanup, provider fake,
backup/restore fixture и inspection файловой системы/очереди/логов. Тест
должен различать policy DENY от технической ошибки, не раскрывая
существование чужого source.
