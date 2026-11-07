export type OfflineCommandStatus =
  | "queued"
  | "syncing"
  | "attention"
  | "auth_required"
  | "conflict";

export type StoredUploadFile = {
  name: string;
  path: string;
  mimeType: string;
  blob: Blob;
};

/**
 * Fields shared by every offline journal command, regardless of operation.
 * These remain available exactly as before on all command variants.
 */
export type OfflineCommandBase = {
  id: string;
  userId: number;
  projectId: number;
  deviceId: string;
  patch: Record<string, unknown>;
  clientCreatedAt: string;
  status: OfflineCommandStatus;
  attempts: number;
  lastError?: string;
  receiptId?: number;
  conflictId?: number;
  updatedAt: string;
};

/**
 * Existing entity-targeted operations. These retain a REQUIRED numeric
 * entityId and keep the existing baseToken / uploads compatibility.
 */
export type OfflineEntityCommand = OfflineCommandBase & {
  operation: "task.update" | "notification.mark_read" | "document.upload";
  entityId: number;
  baseToken?: string;
  uploads?: StoredUploadFile[];
};

/**
 * New work_log.create operation. It is not entity-targeted: entityId,
 * baseToken and uploads are explicitly forbidden (never) so the union can be
 * narrowed safely. The payload lives in `patch`; a future engine maps
 * command.patch -> module prepared.data explicitly (no unsafe cast).
 */
export type OfflineWorkLogCreateCommand = OfflineCommandBase & {
  operation: "work_log.create";
  entityId?: never;
  baseToken?: never;
  uploads?: never;
};

export type OfflineCommand = OfflineEntityCommand | OfflineWorkLogCreateCommand;

export type ServerConflict = {
  id: number;
  command_id: number;
  entity_type: string;
  entity_id: number;
  base_values: Record<string, unknown>;
  local_values: Record<string, unknown>;
  server_values: Record<string, unknown>;
  conflicting_fields: string[];
  server_record_version: number;
  server_updated_at?: string;
  status: "unresolved" | "resolved";
  resolution?: string;
};

export type SyncSummary = {
  queued: number;
  syncing: number;
  attention: number;
  authRequired: number;
  conflicts: number;
  total: number;
  lastSyncedAt?: string;
  lastError?: string;
};

/**
 * Immutable prepared input for a future workLogQueue module.
 *
 * This is intentionally NOT part of the OfflineCommand union and carries no
 * entityId, baseToken, or uploads. It represents a work_log.create payload that
 * has been prepared for delivery but not yet queued. Static typing here does not
 * replace runtime validation of the server contract.
 */
export type PreparedWorkLogCreate = {
  readonly id: string;
  readonly userId: number;
  readonly projectId: number;
  readonly deviceId: string;
  readonly operation: "work_log.create";
  readonly patch: Readonly<Record<string, unknown>>;
  readonly clientCreatedAt: string;
  readonly status: "queued";
  readonly attempts: 0;
  readonly updatedAt: string;
};

/**
 * Optional acknowledgement shape for a work_log.create delivery.
 * Static typing here does not replace runtime validation of the server response.
 */
export type WorkLogCreateAck = {
  readonly receipt_id: number;
  readonly client_mutation_id: string;
  readonly operation: "work_log.create";
  readonly entity_type: "work_log";
  readonly entity_id: number;
  readonly status: "applied";
  readonly result: {
    readonly id: number;
    readonly project_id: number;
    readonly author_user_id: number;
    readonly [key: string]: unknown;
  };
};
