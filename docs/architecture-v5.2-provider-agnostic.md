# PU Workspace architecture v5.2 — provider agnostic

## Decision

PU Workspace Core owns projects, documents, contracts, tasks/actions, approvals,
permissions and audit. It must not import a vendor SDK or require a vendor-specific
identifier to execute domain rules.

External systems are replaceable adapters:

- `IntegrationAdapter` — common identity and health contract;
- `StorageAdapter` — folders/files and content sources;
- `ChannelAdapter` — incoming and outgoing communications;
- `AIProviderAdapter` — document/message analysis behind project AI policy.

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

No additional provider is implemented in this slice. The contracts are the seam
for Yandex 360, VK WorkSpace, Microsoft/Exchange/SharePoint, private file storage,
corporate APIs and private AI endpoints.

## Migration backlog (not part of Vertical Slice 1)

1. Replace task columns named `google_*` with a generic external-link table, preserving data.
2. Replace `DriveConnection` with generic integration accounts/connections via a data migration.
3. Move Telegram notification transport from `core` into a channel adapter.
4. Add deployment profiles for PU Cloud, Russian cloud/private cloud and on-premise.
5. Add provider capability/configuration UI only when a second implementation exists.

These items must be delivered as additive migrations. Existing credentials, external
IDs and production integrations must not be deleted or silently rewritten.
