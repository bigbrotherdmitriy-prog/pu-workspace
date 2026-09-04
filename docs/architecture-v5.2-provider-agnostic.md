# PU Workspace architecture v5.2 — provider agnostic

## Decision

PU Workspace Core owns projects, documents, contracts, tasks/actions, approvals,
permissions and audit. It must not import a vendor SDK or require a vendor-specific
identifier to execute domain rules.

External systems are replaceable adapters:

- `IntegrationAdapter` — common identity and health contract;
- `StorageAdapter` — folders/files and content sources;
- `ChannelAdapter` — incoming and outgoing communications;
- `AIProviderAdapter` — document/message analysis behind project AI policy;
- `ActionAdapter` — publishing approved tasks and deadlines without coupling
  Task Core to an external work-management provider.

`StorageObject` is the provider-neutral boundary object used by Document, Task,
Response and Governance engines. `DriveFile` remains a compatibility alias while
the existing Google adapter is migrated; it is not a Core type.

## Vertical Slice 1

The production Google Workspace, Gmail and Telegram integrations remain enabled.
Gemini is the first `AIProviderAdapter`. Existing safety rules remain unchanged:
original files are not modified, external actions require the established approval
policy, and AI use is governed by the project's `ProjectAIPolicy`.

Google account credentials are resolved by `GoogleWorkspaceAdapter`, shared by
Drive, Gmail, Tasks and Calendar. `DriveClient` is the first `StorageAdapter`.
The old API credential function remains only as a compatibility facade.
Telegram delivery is implemented by `TelegramChannelAdapter`; the old Core
notification module only re-exports compatibility functions.
External task and calendar identifiers are dual-written to the provider-neutral
`external_resource_links` table. Existing `google_*` task columns remain during
the compatibility period and are backfilled by an additive migration.
Task API and Telegram commands publish approved actions through `ActionAdapter`.
`GoogleWorkspaceActionAdapter` is the only Vertical Slice 1 implementation; the
legacy `/tasks/sync-google` route remains as a compatibility alias for the neutral
`/tasks/sync-actions` route.
The approval API accepts provider-neutral `publish_task` and `publish_calendar`
flags. The former Google-specific request names remain accepted as compatibility
aliases, and legacy response fields remain available during the migration period.

No additional provider is implemented in this slice. The contracts are the seam
for Yandex 360, VK WorkSpace, Microsoft/Exchange/SharePoint, private file storage,
corporate APIs and private AI endpoints.

## Migration backlog (not part of Vertical Slice 1)

1. After a monitored compatibility period, read external IDs from the generic link table.
2. Replace `DriveConnection` with generic integration accounts/connections via a data migration.
3. Add deployment profiles for PU Cloud, Russian cloud/private cloud and on-premise.
4. Add provider capability/configuration UI only when a second implementation exists.

These items must be delivered as additive migrations. Existing credentials, external
IDs and production integrations must not be deleted or silently rewritten.
