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

export type OfflineCommand = {
  id: string;
  userId: number;
  projectId: number;
  deviceId: string;
  operation: "task.update" | "notification.mark_read" | "document.upload";
  entityId: number;
  baseToken?: string;
  patch: Record<string, unknown>;
  uploads?: StoredUploadFile[];
  clientCreatedAt: string;
  status: OfflineCommandStatus;
  attempts: number;
  lastError?: string;
  receiptId?: number;
  conflictId?: number;
  updatedAt: string;
};

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
