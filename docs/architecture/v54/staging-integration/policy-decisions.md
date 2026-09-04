# Policy decision table

`ALLOW` требует явных server-side policy pins. `UNKNOWN` всегда
равен `DENY`; user approval не может обойти data policy.

| Copy policy | Derive policy | Retention/backup | Исход |
|---|---|---|---|
| `reference_only` | any | any | Только metadata/reference. Запрещены download, streaming body, staging, temp, OCR, text, quote, embedding, telemetry content, backup и safe-copy. |
| `transient_encrypted` | none | no-retain, no-backup | Разрешён encrypted staging только для declared non-derived operation; purge немедленно по terminal state. |
| `transient_encrypted` | OCR only | bounded, no-backup | Разрешены staging + OCR temp/raster/text только в declared processing location; каждый artifact в manifest; evidence с actual representation. |
| `transient_encrypted` | indexing/embedding denied | bounded | OCR может выполниться, но index/embedding не создаются. |
| `authorized_copy` | explicit allowlist | policy-defined | Copy получает собственный external object/ref/version; она не original. |
| any allow | any | backup `unknown`/denied | Backup исключает representation и keys. Если инфраструктура не умеет доказуемо исключить, admission denied. |
| any allow | any | legal hold conflicts with no-retain | BLOCKED pending owner/legal decision; legal hold не расширяет live access. |
| allow at admission | any | policy/identity/source revoked before read | Не decrypt/read; cancel and purge according to now-effective policy. |
| allow at read | any | revoke during processing | Continue/commit только при explicit offline-processing grant, иначе abort/purge. |
| allow | external AI denied | any | Local processing only; no provider upload, prompt quote or remote logs. |
| allow | safe-copy absent/denied | any | Snapshot metadata may exist; `_start_safe_copy_pipeline` must not run. |

## Policy decision record

Минимальный immutable decision привязан к:

- organization/project, actor/service identity and authority epoch;
- ConnectionIdentity and binding epoch;
- SourceReference + exact SourceVersion;
- purpose/operation and allowed representation/derive classes;
- processing/residency location;
- access/copy/derive/retention/backup policy IDs and versions;
- `admitted_at`, `valid_until`, decision digest and reason code.

В записи нет URL, filename, bytes, text, quote, token, provider raw error
или unrestricted hash. Решение повторно проверяется worker-ом;
сама запись ALLOW не замораживает отозванные права.
