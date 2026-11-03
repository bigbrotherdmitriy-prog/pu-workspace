import { api, ApiError } from "../api/client";
import { isOnline } from "./connectivity";
import { deleteCommand, getMeta, listCommands, putCommand, setMeta } from "./db";
import type { OfflineCommand, ServerConflict, StoredUploadFile, SyncSummary } from "./types";

const ATTENTION_AFTER_ATTEMPTS = 3;
export const CHANGE_EVENT = "pu-mobile-sync-changed";
export const WORK_LOG_APPLIED_EVENT = "pu-work-log-applied";
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

/**
 * A queued command may be safely coalesced only when it has never been sent:
 * no attempts, no receipt, no conflict. Once a command has been attempted (or
 * is syncing/attention/auth_required/conflict) its payload is immutable, because
 * the server may already have applied it and compares the stored payload on
 * retry (client_mutation_id_reused on mismatch).
 */
function isSafeToCoalesce(command: OfflineCommand): boolean {
  return command.status === "queued"
    && command.attempts === 0
    && command.receiptId === undefined
    && command.conflictId === undefined;
}

function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

/** Explicit recovery only; ordinary/manual synchronize still skips auth_required. */
export async function recoverAuthRequired(userId: number, projectId: number): Promise<number> {
  if (!positiveId(userId) || !positiveId(projectId)) throw new Error("Нужны пользователь и проект для восстановления очереди.");
  if (!isOnline()) throw new Error("Для проверки входа требуется связь с сервером.");
  if (!(await listCommands(userId, projectId)).some((row) => row.status === "auth_required")) return 0;
  const actor = await api<{ id?: unknown }>("/auth/me");
  if (actor?.id !== userId) throw new Error("Войдите в ту же учётную запись, в которой сохранены изменения.");
  // Reload after the identity check rather than resurrecting an already removed row.
  const commands = (await listCommands(userId, projectId)).filter((row) => row.status === "auth_required");
  for (const command of commands) {
    await persist({ ...command, status: "queued", lastError: undefined, updatedAt: new Date().toISOString() });
  }
  return commands.length;
}

export type PreparedWorkLogCreate = Readonly<Extract<OfflineCommand, { operation: "work_log.create" }>>;

function freezeJson(value: unknown): void {
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) freezeJson(item);
    Object.freeze(value);
  }
}

function workLogEnvelope(command: PreparedWorkLogCreate) {
  return {
    client_mutation_id: command.id, device_id: command.deviceId,
    project_id: command.projectId, operation: command.operation,
    client_created_at: command.clientCreatedAt, data: command.patch,
  };
}

export function prepareWorkLogCreate(args: {
  userId: number; projectId: number; data: Record<string, unknown>;
}): PreparedWorkLogCreate {
  if (!positiveId(args.userId) || !positiveId(args.projectId)) throw new Error("Нужны пользователь и проект для локальной записи.");
  const day = args.data.work_date;
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day) || Number(day.slice(0, 4)) < 1) {
    throw new Error("Укажите дату выполненной работы в формате ГГГГ-ММ-ДД.");
  }
  const parsedDay = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsedDay.getTime()) || parsedDay.toISOString().slice(0, 10) !== day) throw new Error("Некорректная дата выполненной работы.");
  if (typeof args.data.work_name !== "string" || !args.data.work_name.trim()) throw new Error("Укажите выполненную работу.");
  const data = JSON.parse(JSON.stringify(args.data, (_key, value: unknown) => {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Число должно быть конечным.");
    return value;
  })) as Record<string, unknown>;
  const device = deviceId();
  if (!/^[A-Za-z0-9_.:-]{8,100}$/.test(device)) throw new Error("Некорректный идентификатор устройства.");
  const now = new Date().toISOString(), id = uuid();
  // Every create is a distinct fact. Never coalesce or invent a server entity id.
  freezeJson(data);
  const command: Extract<OfflineCommand, { operation: "work_log.create" }> = {
    id, userId: args.userId, projectId: args.projectId, deviceId: device,
    operation: "work_log.create", patch: data, clientCreatedAt: now,
    status: "queued", attempts: 0, updatedAt: now,
  };
  return Object.freeze(command);
}

export async function enqueuePreparedWorkLogCreate(command: PreparedWorkLogCreate): Promise<string> {
  if (command.operation !== "work_log.create" || !positiveId(command.userId) || !positiveId(command.projectId) || "entityId" in command) {
    throw new Error("Некорректная подготовленная запись журнала.");
  }
  const existing = (await listCommands(command.userId, command.projectId)).find((row) => row.id === command.id);
  if (existing) {
    if (existing.operation !== "work_log.create" || JSON.stringify(workLogEnvelope(existing)) !== JSON.stringify(workLogEnvelope(command))) {
      throw new Error("Идентификатор записи уже использован с другими данными.");
    }
    // Preserve attempts, receipt and suspended status; this is not auth recovery.
    return existing.id;
  }
  await persist({ ...command, patch: JSON.parse(JSON.stringify(command.patch)) as Record<string, unknown> });
  return command.id;
}

export async function enqueueWorkLogCreate(args: {
  userId: number; projectId: number; data: Record<string, unknown>;
}): Promise<string> {
  return enqueuePreparedWorkLogCreate(prepareWorkLogCreate(args));
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
    item.operation === "task.update" && item.entityId === args.taskId && isSafeToCoalesce(item),
  );
  if (existing) {
    // Keep the original baseToken, clientCreatedAt and attempts: the merged
    // payload is still the first (never-sent) attempt for this id.
    await persist({
      ...existing,
      patch: { ...existing.patch, ...args.patch },
      updatedAt: now,
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

async function submit(command: OfflineCommand): Promise<{ applied: boolean; conflictId?: number; receiptId?: number; entityId?: number }> {
  if (command.operation === "work_log.create") {
    const response = await api<{
      receipt_id?: unknown; client_mutation_id?: unknown; operation?: unknown;
      status?: unknown; entity_type?: unknown; entity_id?: unknown;
      result?: { id?: unknown; project_id?: unknown; author_user_id?: unknown };
    }>("/mobile-sync/work-log-create", {
      method: "POST", body: JSON.stringify(workLogEnvelope(command)),
      headers: { "X-PU-Expected-User": String(command.userId) },
    });
    if (response?.status !== "applied" || response.operation !== "work_log.create"
      || response.client_mutation_id !== command.id || response.entity_type !== "work_log"
      || !positiveId(response.receipt_id) || !positiveId(response.entity_id)
      || response.result?.id !== response.entity_id || response.result?.project_id !== command.projectId
      || response.result?.author_user_id !== command.userId) {
      throw new Error("Не подтверждена доставка записи журнала.");
    }
    return { applied: true, receiptId: response.receipt_id, entityId: response.entity_id };
  }
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

export async function submitPreparedWorkLogCreate(command: PreparedWorkLogCreate): Promise<{ receiptId: number; entityId: number }> {
  if (command.operation !== "work_log.create") throw new Error("Ожидалась подготовленная запись журнала.");
  const result = await submit(command);
  if (!result.applied || !positiveId(result.receiptId) || !positiveId(result.entityId)) {
    throw new Error("Не подтверждена доставка записи журнала.");
  }
  return { receiptId: result.receiptId, entityId: result.entityId };
}

export async function synchronize(userId: number, projectId: number, manual = false): Promise<void> {
  if (running || !isOnline() || !userId || !projectId) return;
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
          if (working.operation === "work_log.create" && positiveId(result.entityId)) {
            window.dispatchEvent(new CustomEvent(WORK_LOG_APPLIED_EVENT, {
              detail: { userId, projectId, entryId: result.entityId, clientMutationId: working.id },
            }));
          }
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
  if (!isOnline() || !projectId) return [];
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
