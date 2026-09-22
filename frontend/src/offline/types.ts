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
