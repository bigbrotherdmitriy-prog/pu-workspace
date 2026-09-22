import { api, ApiError } from "../api/client";
import { deleteCommand, getMeta, listCommands, putCommand, setMeta } from "./db";
import type { OfflineCommand, ServerConflict, StoredUploadFile, SyncSummary } from "./types";

const ATTENTION_AFTER_ATTEMPTS = 3;
export const CHANGE_EVENT = "pu-mobile-sync-changed";
let running = false;

function changed() {
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

function uuid(): string {
  return globalThis.crypto?.randomUUID?.() || `mobile-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function deviceId(): string {
  const key = "pu-mobile-device-id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const value = uuid();
  localStorage.setItem(key, value);
  return value;
}

async function persist(command: OfflineCommand) {
  try {
    await putCommand(command);
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "QuotaExceededError") {
      throw new Error("На устройстве закончилось место. Изменение не сохранено; освободите память и повторите.");
    }
    throw reason;
  }
  changed();
}

export async function enqueueTaskUpdate(args: {
  userId: number;
  projectId: number;
  taskId: number;
  baseToken?: string;
  patch: Record<string, unknown>;
}): Promise<string> {
  if (!args.baseToken) throw new Error("Задача загружена без защищённой базовой версии. Обновите данные онлайн.");
  const now = new Date().toISOString();
  const existing = (await listCommands(args.userId, args.projectId)).find((item) =>
    item.operation === "task.update" && item.entityId === args.taskId
      && ["queued", "attention", "auth_required"].includes(item.status),
  );
  if (existing) {
    await persist({
      ...existing,
      patch: { ...existing.patch, ...args.patch },
      clientCreatedAt: now,
      status: "queued",
      attempts: 0,
      updatedAt: now,
      lastError: undefined,
    });
    return existing.id;
  }
  const id = uuid();
  await persist({
    id,
    userId: args.userId,
    projectId: args.projectId,
    deviceId: deviceId(),
    operation: "task.update",
    entityId: args.taskId,
    baseToken: args.baseToken,
    patch: args.patch,
    clientCreatedAt: now,
    status: "queued",
    attempts: 0,
    updatedAt: now,
  });
  return id;
}

export async function enqueueNotificationRead(args: {
  userId: number;
  projectId: number;
  notificationId: number;
}): Promise<string> {
  const duplicate = (await listCommands(args.userId, args.projectId)).find((item) =>
    item.operation === "notification.mark_read" && item.entityId === args.notificationId,
  );
  if (duplicate) return duplicate.id;
  const now = new Date().toISOString(), id = uuid();
  await persist({
    id,
    userId: args.userId,
    projectId: args.projectId,
    deviceId: deviceId(),
    operation: "notification.mark_read",
    entityId: args.notificationId,
    patch: {},
    clientCreatedAt: now,
    status: "queued",
    attempts: 0,
    updatedAt: now,
  });
  return id;
}

export async function enqueueDocumentUpload(args: {
  userId: number;
  projectId: number;
  files: StoredUploadFile[];
}): Promise<string> {
  const now = new Date().toISOString(), id = uuid();
  if (navigator.storage?.persist) await navigator.storage.persist().catch(() => false);
  await persist({
    id,
    userId: args.userId,
    projectId: args.projectId,
    deviceId: deviceId(),
    operation: "document.upload",
    entityId: 0,
    patch: {},
    uploads: args.files,
    clientCreatedAt: now,
    status: "queued",
    attempts: 0,
    updatedAt: now,
  });
  return id;
}

function blobBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Не удалось прочитать сохранённый файл"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(blob);
  });
}

async function submit(command: OfflineCommand): Promise<{ applied: boolean; conflictId?: number; receiptId?: number }> {
  if (command.operation === "document.upload") {
    const files = await Promise.all((command.uploads || []).map(async (file) => ({
      path: file.path,
      mime_type: file.mimeType,
      content_base64: await blobBase64(file.blob),
    })));
    await api("/local-upload/analyze", {
      method: "POST",
      headers: { "Idempotency-Key": command.id },
      body: JSON.stringify({ project_id: command.projectId, files }),
    });
    return { applied: true };
  }
  const response = await api<{
    receipt_id: number;
    status: string;
    error_code?: string;
    error_message?: string;
    conflict?: { id: number };
  }>("/mobile-sync/commands", {
    method: "POST",
    body: JSON.stringify({
      client_mutation_id: command.id,
      device_id: command.deviceId,
      project_id: command.projectId,
      operation: command.operation,
      entity_id: command.entityId,
      base_token: command.baseToken,
      patch: command.patch,
      client_created_at: command.clientCreatedAt,
    }),
  });
  if (response.status === "applied") return { applied: true, receiptId: response.receipt_id };
  if (response.status === "conflict") {
    return { applied: false, conflictId: response.conflict?.id, receiptId: response.receipt_id };
  }
  throw new Error(response.error_message || response.error_code || "Сервер отклонил офлайн-изменение");
}

export async function synchronize(userId: number, projectId: number, manual = false): Promise<void> {
  if (running || !navigator.onLine || !userId || !projectId) return;
  running = true;
  try {
    const commands = await listCommands(userId, projectId);
    for (const command of commands) {
      if (["conflict", "auth_required"].includes(command.status)) continue;
      if (command.status === "attention" && !manual) continue;
      const working = { ...command, status: "syncing" as const, updatedAt: new Date().toISOString() };
      await persist(working);
      try {
        const result = await submit(working);
        if (result.applied) {
          await deleteCommand(working.id);
          await setMeta(`last-synced:${userId}:${projectId}`, new Date().toISOString());
          changed();
        } else {
          await persist({
            ...working,
            status: "conflict",
            receiptId: result.receiptId,
            conflictId: result.conflictId,
            updatedAt: new Date().toISOString(),
          });
        }
      } catch (reason) {
        const attempts = working.attempts + 1;
        const authRequired = reason instanceof ApiError && [401, 403].includes(reason.status || 0);
        await persist({
          ...working,
          attempts,
          status: authRequired ? "auth_required" : attempts >= ATTENTION_AFTER_ATTEMPTS ? "attention" : "queued",
          lastError: (reason as Error).message,
          updatedAt: new Date().toISOString(),
        });
      }
    }
  } finally {
    running = false;
    changed();
  }
}

export async function offlineSummary(userId: number, projectId: number): Promise<SyncSummary> {
  const commands = userId && projectId ? await listCommands(userId, projectId) : [];
  const lastSyncedAt = userId && projectId
    ? await getMeta<string>(`last-synced:${userId}:${projectId}`)
    : undefined;
  const count = (status: OfflineCommand["status"]) => commands.filter((item) => item.status === status).length;
  return {
    queued: count("queued"),
    syncing: count("syncing"),
    attention: count("attention"),
    authRequired: count("auth_required"),
    conflicts: count("conflict"),
    total: commands.length,
    lastSyncedAt,
    lastError: commands.find((item) => item.lastError)?.lastError,
  };
}

export async function serverConflicts(projectId: number): Promise<ServerConflict[]> {
  if (!navigator.onLine || !projectId) return [];
  const result = await api<{ conflicts: ServerConflict[] }>(`/mobile-sync/status?project_id=${projectId}`);
  return result.conflicts;
}

export async function resolveConflict(
  userId: number,
  projectId: number,
  conflictId: number,
  resolution: "keep_server" | "apply_local" | "latest_write_wins",
): Promise<void> {
  await api(`/mobile-sync/conflicts/${conflictId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution }),
  });
  const commands = await listCommands(userId, projectId);
  const local = commands.find((item) => item.conflictId === conflictId);
  if (local) await deleteCommand(local.id);
  await setMeta(`last-synced:${userId}:${projectId}`, new Date().toISOString());
  changed();
}
