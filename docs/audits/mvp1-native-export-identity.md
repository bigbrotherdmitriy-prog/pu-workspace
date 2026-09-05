# D05: bounded native-export identity/cache guard

Base: `2848bba170025fbc84d3d3d7bff35d1e19e13739`. Offline implementation; D05 remains **partial / transport integration blocked**, not closed.

## Existing path and gap

`DriveClient.populate_content` and `read_bytes` export the current native document using `files.export_media(fileId, mimeType)`. They do not obtain an exact original revision, bind it atomically to the returned bytes, or maintain an export representation cache. Existing legacy ingestion can record modified time and an extracted-text digest; that is not an original native revision proof.

Google's documented [files.export](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/export) accepts a file ID and output MIME, not a revision selector. The [Revision resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/revisions) includes export links, but this repository has no verified revision-export transport implementing their identity, authentication and expiry semantics. A metadata read before/after latest export is not introduced as an atomic precondition.

## Change and reuse

`app/integrations/native_export.py` is a read-only adapter over existing `SourceReference`, immutable `SourceVersion`, `Evidence` and encrypted `Materialization` rows. It adds no table, queue, cache registry, staging implementation, route, migration or OAuth behavior.

The materialization owner remains responsible for creating/sealing/deriving an eligible representation. The source-version owner must already have recorded:

- the original source external ID (`opaque`) and exact `provider_revision`, with `consistency=revision_bound`;
- `locator_at_observation` with `schema_version=native-export.observation.1`, original native MIME, exported MIME and exported byte length;
- one SHA256 integrity entry describing **exported bytes**, not the native original.

This is an explicit future owner interface, not evidence that a current Google producer satisfies it. The adapter has no API accepting caller-provided proof: caller IDs/revision/MIME/pins are expectations checked against stored rows. Legacy observations cannot satisfy this contract by supplying a hash or modified-time string to the read call.

The output manifest is a projection containing original ID/revision/MIME, export SHA256/MIME/size, existing materialization manifest and absolute expiry. Cache identity includes tenant/project/owner, immutable source/version and export identity. Repeating a read of the same representation creates no rows, advances no version and extends no TTL.

Before storage I/O, the adapter checks existing lifecycle copy authorization (both stored grant and current authority), exact pins, scope, source-current pointer, source availability/freshness, identity status/binding epoch/account, explicit format/size and retention. It verifies byte length/SHA256 after read and rechecks retention/current policy before returning. Missing, stale, expired or mismatched cache entries fail with the fixed `resource_unavailable`; there is no latest/provider fallback and no raw content or provider error logging.

`DriveClient.read_native_export_exact` explicitly rejects with fixed `native_export_revision_unavailable` before any provider request. `supports_exact_native_export=False` is truthful. Existing best-effort native reads and scans remain unchanged and do not acquire this guarantee.

## Open integration request: IR-D05-EXACT-NATIVE-TRANSPORT

The storage/source owner must provide a verified revision-addressed native export implementation, bind original revision to actual exported bytes, and write the immutable observation plus admitted materialization using existing lifecycle policy/retention authority. Neither client-supplied assertions nor a latest-export metadata sandwich are sufficient. Until this is implemented and provider-contract tested, exact native requests remain denied. Production owner-policy wiring and end-to-end native ingestion acceptance are not claimed by this adapter.

## Validation

- First RED: **14 failures** before implementation (13 missing native cache contracts, one missing Drive exact-native boundary).
- After implementation: **26 new tests pass**, including real SQLite Source/Evidence rows and actual encrypted filesystem materialization admit/write/seal/derive/read, stable repeated result/no new rows, original ID/revision/version/MIME mismatch, same-length export hash mismatch, current change, TTL expiry before/during read, revoked identity, no-copy before bytes, unsupported legacy observations and missing cache.
- Adjacent targeted run: **111 passed** across the new suite, lifecycle, Drive safety, storage provider regression/primitives/live-adapter doubles and Source/Evidence pilot tests.
- No Google/provider requests, real documents, external AI, production data, PostgreSQL or full backend suite executed for this change. Tests use local synthetic data and provider/storage doubles only where boundary effects are asserted. This is not a successful live native export or full D05 proof.
