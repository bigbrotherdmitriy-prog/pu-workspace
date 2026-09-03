# Lifecycle encrypted materialization

## Состояния

```text
REQUESTED
  ├─ deny/unknown policy → DENIED (no download)
  └─ allow + exact pins → ADMITTED
       └─ provider read → WRITING → READY
            └─ durable job binding → QUEUED → PROCESSING
                 ├─ derived outputs committed → COMPLETED → PURGE_DUE
                 ├─ retryable failure → RETAINED_FOR_RETRY → QUEUED
                 ├─ cancel/revoke/expiry → PURGE_DUE
                 └─ terminal failure → PURGE_DUE
PURGE_DUE → PURGING → PURGED
                      └─ failure → PURGE_FAILED → fenced retry/operator alert
```

`EXPIRED` не считается фактом удаления. Только `PURGED`, записанный
после идемпотентного удаления всех declared artifacts, означает
завершённый cleanup. Для недостижимой backup-копии состояние
остаётся `PURGE_FAILED` до политически корректного исхода.

## Точки перепроверки

| Точка | Обязательная проверка | При отказе |
|---|---|---|
| До provider download | live actor permission, exact SourceVersion, access/copy/derive/residency/retention/backup pins | DENIED, zero bytes |
| После provider headers, до body | provider revision/ETag/content length/type не противоречат pin/policy | Abort stream, no retry as same version |
| Перед READY | authenticated encryption complete, observed digest/size, source observation decision | Delete partial or quarantine without read |
| Перед enqueue | representation and request committed; existing queue gets opaque request ID | Recovery enqueues same request, no second copy |
| После worker claim, до decrypt | live lease + scope + binding epoch + source/policy state + retention window | Cancel/PURGE_DUE |
| Перед каждым derive class | class allowlisted: OCR raster/text/quote/cache/embedding/AI | Skip/blocked result; no implicit upgrade |
| Перед commit evidence | evidence points to actual representation and exact SourceVersion | Roll back derived metadata; purge artifacts |
| Перед retry/recovery | same request identity, policy remains usable, representation intact | New admission/read required; no blind retry |
| Перед backup restore read | tombstone/revoke/purge replayed, key/policy still usable | Block read and purge/quarantine |

## Crash, lease и duplicate worker

- `materialization_request_id` стабилен; queue attempt/worker/lease меняются.
- Перед чтением worker получает dispatch binding/fence для этой попытки.
  Потеря lease лишает его права на новые reads/commits.
- Повторный worker reuse-ит ту же READY representation; новая encrypted
  copy не создаётся. Если representation была purged, нужен новый
  policy admission и provider read, а не resurrection.
- Artifact manifest должен регистрировать staging ciphertext, partial,
  plaintext temp, OCR raster, extracted text/cache/embedding до их создания.
  Cleanup идёт по manifest, а не по glob всего volume.

## Key loss и rotation

Key version и KEK reference хранятся как metadata, сам key — в secret manager.
Rotation не перешифровывает plaintext: DEK rewrap возможен лишь для
разрешённой неистёкшей representation. Потеря ключа даёт
`UNREADABLE → PURGE_DUE/reacquire with new admission`, а не fallback к original
без policy. Удаление DEK/KEK не отменяет physical cleanup и backups.

## Revocation during processing

Если source/provider access revoked до decrypt, worker ничего не читает.
Если revoke пришёл после read, policy отдельно определяет,
разрешены ли offline processing и commit. При absent/deny worker
прекращает derive, не публикует evidence и запускает cleanup.
