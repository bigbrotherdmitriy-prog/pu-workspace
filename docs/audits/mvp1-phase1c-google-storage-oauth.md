# MVP-1 Phase 1c — Google storage OAuth port

Date: 2026-09-09

Branch: `codex/v7-wbs-wave7`

Base: `3d62af1cbf96d0bed73d8a388a43c0bbbe9330f3`

## Scope and result

Phase 1c adds only a Google Drive OAuth port for the isolated MVP-1 application
and a provider-neutral `StorageCredentialPort`. It does not add Yandex-specific
OAuth code and it does not begin Phase 2 / D02-D15.

The OAuth port requests only `openid`, `email`, and Google Drive. Its client
configuration is independent from the legacy Google Workspace/mailbox flow.
OAuth state is random, hashed at rest, server-side, short-lived, single-use, and
bound to the exact user, project, and organization.

The callback stores encrypted tokens in the existing credential table and
creates or refreshes the project's `DriveConnection`. Folder discovery can
therefore start at `root`; the subsequent binding call persists the selected
folder and the exact opaque `connection_id`.

## Separation achieved

- MVP-1 no longer needs the mailbox-identity OAuth router to authorize Drive.
- The credential lookup contract is provider-neutral and can be implemented for
  another provider later without adding provider-specific code now.
- Google storage access uses the exact `connection_id`; it does not silently
  select another token by `project_id`.
- Existing legacy connections remain supported only through an exact
  `google-token:{id}` reference, or through the old project lookup when a legacy
  row has no `connection_id` at all.
- Binding verifies credential/project/organization ownership and ignores a
  caller-supplied Google account email in favour of verified identity.

## Evidence

- Targeted OAuth/storage/migration regression: `85 passed, 1 skipped`.
- Schema/readiness regression: `136 passed, 4 skipped`.
- CI contract regression: `99 passed`.
- Full backend: **`2498 passed, 65 skipped`**, no failures, 2380.91 seconds.
- Alembic has one head: `a54f001c0a22`.
- `CURRENT_SCHEMA_REVISION` and current CI/readiness pins use `a54f001c0a22`.

The opt-in live test `test_mvp1_google_storage_live.py` is deliberately skipped
unless `PU_MVP1_GOOGLE_LIVE_TEST=1` and dedicated test credentials/file/folder
are supplied. In one scenario it first proves cross-project and cross-tenant
denial, then copies the designated test file, reads its metadata, renames only
the test copy, and trashes that copy in cleanup. No production credentials or
client documents were used in this run, so live-provider evidence remains
**NOT RUN**, not PASS.

## Configuration

- `GOOGLE_STORAGE_CLIENT_ID`
- `GOOGLE_STORAGE_CLIENT_SECRET`
- `GOOGLE_STORAGE_REDIRECT_URI`
- existing `TOKEN_ENCRYPTION_KEY`

## Remaining entanglement and limitations

- `IntegrationCredential` is reused as the durable encrypted record; this is an
  intentional shared persistence primitive, not a mailbox OAuth dependency.
- The full application also exposes the MVP-1 storage OAuth router for backward
  compatible operation; the isolated composition exposes only the MVP-1 port.
- A real provider run still requires an explicitly provisioned test Google
  account and test folder. It must not use production data.
- Yandex-specific OAuth, Phase 2 document cases D02-D15, and production rollout
  are outside this phase and were not started.
