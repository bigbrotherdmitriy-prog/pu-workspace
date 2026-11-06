import { api, ApiError } from "../api/client";
import { isOnline } from "./connectivity";
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

function isPositiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function expectedEntityType(command: OfflineCommand): string {
  return command.operation === "task.update" ? "task" : "notification";
}

function receiptError(message: string): Error {
  return new Error(`Некорректная квитанция сервера: ${message}`);
}

const UPLOAD_JOB_STATUSES = ["queued", "retrying", "running", "already_queued"] as const;
type UploadJobStatus = (typeof UPLOAD_JOB_STATUSES)[number];
const STAGING_ID_PATTERN = /^[0-9a-f]{32}$/;

type UploadStagingRef = { job_id: number; staging_id: string; status: UploadJobStatus };
type UploadStagingAck = { status: string; jobs: UploadStagingRef[] };

function uploadAckError(message: string): Error {
  return new Error(`Некорректное подтверждение загрузки документа: ${message}`);
}

function isUploadJobStatus(value: unknown): value is UploadJobStatus {
  return typeof value === "string" && (UPLOAD_JOB_STATUSES as readonly string[]).includes(value);
}

function isStagingId(value: unknown): value is string {
  return typeof value === "string" && STAGING_ID_PATTERN.test(value);
}

function validateUploadAck(response: unknown, expectedCount: number): UploadStagingAck {
  if (!response || typeof response !== "object" || Array.isArray(response)) {
    throw uploadAckError("ответ не является объектом");
  }
  const ack = response as Record<string, unknown>;
  if (typeof ack.status !== "string" || ack.status !== "queued") {
    throw uploadAckError("некорректный статус анализа");
  }
  if (!Array.isArray(ack.jobs)) {
    throw uploadAckError("отсутствует список заданий");
  }
  if (ack.jobs.length !== expectedCount) {
    throw uploadAckError("количество заданий не совпадает с числом файлов");
  }
  const jobIds = new Set<number>();
  const stagingIds = new Set<string>();
  const jobs: UploadStagingRef[] = [];
  for (const entry of ack.jobs) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw uploadAckError("задание не является объектом");
    }
    const job = entry as Record<string, unknown>;
    if (!isPositiveSafeInteger(job.job_id)) {
      throw uploadAckError("некорректный job_id");
    }
    if (!isStagingId(job.staging_id)) {
      throw uploadAckError("некорректный staging_id");
    }
    if (!isUploadJobStatus(job.status)) {
      throw uploadAckError("некорректный статус задания");
    }
    if (jobIds.has(job.job_id)) {
      throw uploadAckError("повторяющийся job_id");
    }
    if (stagingIds.has(job.staging_id)) {
      throw uploadAckError("повторяющийся staging_id");
    }
    jobIds.add(job.job_id);
    stagingIds.add(job.staging_id);
    jobs.push({ job_id: job.job_id, staging_id: job.staging_id, status: job.status });
  }
  return { status: ack.status, jobs };
}

async function submit(command: OfflineCommand): Promise<{ applied: boolean; conflictId?: number; receiptId?: number }> {
  if (command.operation === "document.upload") {
    const uploads = command.uploads || [];
    if (uploads.length === 0) {
      throw uploadAckError("нет файлов для загрузки");
    }
    const files = await Promise.all(uploads.map(async (file) => ({
      path: file.path,
      mime_type: file.mimeType,
      content_base64: await blobBase64(file.blob),
    })));
    const response = await api<unknown>("/local-upload/analyze", {
      method: "POST",
      headers: {
        "Idempotency-Key": command.id,
        "X-PU-Expected-User": String(command.userId),
      },
      body: JSON.stringify({ project_id: command.projectId, files }),
    });
    const ack = validateUploadAck(response, uploads.length);
    await setMeta(`upload-staging-ack:${command.userId}:${command.projectId}:${command.id}`, {
      status: ack.status,
      jobs: ack.jobs.map((job) => ({ job_id: job.job_id, staging_id: job.staging_id, status: job.status })),
    });
    return { applied: true };
  }
  const response = await api<{
    receipt_id: number;
    client_mutation_id: string;
    operation: string;
    entity_id: number;
    entity_type: string;
    status: string;
    result?: unknown;
    error_code?: string;
    error_message?: string;
    conflict?: {
      id: number;
      command_id: number;
      entity_type: string;
      entity_id: number;
    };
    created_at?: string;
    updated_at?: string;
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
  if (!response || typeof response !== "object") {
    throw receiptError("ответ не является объектом");
  }
  if (response.status !== "applied" && response.status !== "conflict") {
    throw new Error(response.error_message || response.error_code || "Сервер отклонил офлайн-изменение");
  }
  if (!isPositiveSafeInteger(response.receipt_id)) {
    throw receiptError("отсутствует корректный receipt_id");
  }
  if (response.client_mutation_id !== command.id) {
    throw receiptError("client_mutation_id не совпадает с командой");
  }
  if (response.operation !== command.operation) {
    throw receiptError("operation не совпадает с командой");
  }
  if (response.entity_id !== command.entityId) {
    throw receiptError("entity_id не совпадает с командой");
  }
  if (response.entity_type !== expectedEntityType(command)) {
    throw receiptError("entity_type не совпадает с командой");
  }
  if (response.status === "applied") return { applied: true, receiptId: response.receipt_id };
  const conflict = response.conflict;
  if (!conflict || typeof conflict !== "object") {
    throw receiptError("отсутствует описание конфликта");
  }
  if (!isPositiveSafeInteger(conflict.id)) {
    throw receiptError("отсутствует корректный conflict.id");
  }
  if (conflict.command_id !== response.receipt_id) {
    throw receiptError("conflict.command_id не совпадает с receipt_id");
  }
  if (conflict.entity_type !== response.entity_type) {
    throw receiptError("conflict.entity_type не совпадает с квитанцией");
  }
  if (conflict.entity_id !== response.entity_id) {
    throw receiptError("conflict.entity_id не совпадает с квитанцией");
  }
  return { applied: false, conflictId: conflict.id, receiptId: response.receipt_id };
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

const RESOLUTION_VALUES = ["keep_server", "apply_local", "latest_write_wins"] as const;
type ConflictResolution = (typeof RESOLUTION_VALUES)[number];
const SUPPORTED_ENTITY_TYPES = ["task", "notification"] as const;

function isSupportedEntityType(value: unknown): value is string {
  return typeof value === "string" && (SUPPORTED_ENTITY_TYPES as readonly string[]).includes(value);
}

function isSupportedResolution(value: unknown): value is ConflictResolution {
  return typeof value === "string" && (RESOLUTION_VALUES as readonly string[]).includes(value);
}

function conflictAckError(message: string): Error {
  return new Error(`Некорректное подтверждение разрешения конфликта: ${message}`);
}

export async function resolveConflict(
  userId: number,
  projectId: number,
  conflictId: number,
  resolution: "keep_server" | "apply_local" | "latest_write_wins",
): Promise<void> {
  if (!isPositiveSafeInteger(userId)) throw conflictAckError("некорректный userId");
  if (!isPositiveSafeInteger(projectId)) throw conflictAckError("некорректный projectId");
  if (!isPositiveSafeInteger(conflictId)) throw conflictAckError("некорректный conflictId");
  if (!isSupportedResolution(resolution)) throw conflictAckError("неподдерживаемое разрешение конфликта");

  const before = await listCommands(userId, projectId);
  const local = before.find((item) => item.conflictId === conflictId);

  const response = await api<{
    id: number;
    command_id: number;
    entity_type: string;
    entity_id: number;
    status: string;
    resolution?: string;
  }>(`/mobile-sync/conflicts/${conflictId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution }),
  });

  if (!response || typeof response !== "object" || Array.isArray(response)) {
    throw conflictAckError("ответ не является объектом");
  }
  if (!isPositiveSafeInteger(response.id)) {
    throw conflictAckError("отсутствует корректный id конфликта");
  }
  if (response.id !== conflictId) {
    throw conflictAckError("id конфликта не совпадает с запрошенным");
  }
  if (!isPositiveSafeInteger(response.command_id)) {
    throw conflictAckError("отсутствует корректный command_id");
  }
  if (!isPositiveSafeInteger(response.entity_id)) {
    throw conflictAckError("отсутствует корректный entity_id");
  }
  if (!isSupportedEntityType(response.entity_type)) {
    throw conflictAckError("неподдерживаемый entity_type");
  }
  if (response.status !== "resolved") {
    throw conflictAckError("конфликт не разрешён");
  }
  if (!isSupportedResolution(response.resolution)) {
    throw conflictAckError("отсутствует корректное разрешение конфликта");
  }

  if (local) {
    if (!isPositiveSafeInteger(local.receiptId)) {
      throw conflictAckError("у локальной команды отсутствует receiptId");
    }
    if (response.command_id !== local.receiptId) {
      throw conflictAckError("command_id не совпадает с локальной квитанцией");
    }
    if (response.entity_type !== expectedEntityType(local)) {
      throw conflictAckError("entity_type не совпадает с локальной командой");
    }
    if (response.entity_id !== local.entityId) {
      throw conflictAckError("entity_id не совпадает с локальной командой");
    }
    const current = (await listCommands(userId, projectId)).find((item) => item.id === local.id);
    if (!current || current.conflictId !== conflictId || current.receiptId !== local.receiptId) {
      throw conflictAckError("локальная команда изменилась до подтверждения");
    }
    await deleteCommand(current.id);
  }

  await setMeta(`last-synced:${userId}:${projectId}`, new Date().toISOString());
  changed();
}
