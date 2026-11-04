import { api, ApiError } from "../api/client";
import { isOnline } from "./connectivity";
import { deleteCommand, getMeta, listCommands, putCommand, putCommands, setMeta } from "./db";
import type { OfflineCommand, ServerConflict, StoredUploadFile, SyncSummary } from "./types";

const ATTENTION_AFTER_ATTEMPTS = 3;
export const CHANGE_EVENT = "pu-mobile-sync-changed";
export const WORK_LOG_APPLIED_EVENT = "pu-work-log-applied";
export const DEFECT_APPLIED_EVENT = "pu-defect-applied";
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
export type PreparedDefectCreate = Readonly<Extract<OfflineCommand, { operation: "defect.create" }>>;

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

const DEFECT_TEXT_LIMITS: Record<string, number> = { title: 500, description: 5000, object_name: 300, location: 300, responsible: 300 };
const DEFECT_FIELD_LABELS: Record<string, string> = { title: "Название замечания", description: "Подробности", object_name: "Объект", location: "Место", responsible: "Ответственный" };
const DEFECT_FIELDS = new Set([...Object.keys(DEFECT_TEXT_LIMITS), "priority", "due_date"]);

function checkedDefectData(value: Record<string, unknown>): Record<string, unknown> {
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("Некорректные данные замечания.");
  if (typeof value.title !== "string" || !value.title.trim()) throw new Error("Опишите замечание коротко: что не так.");
  for (const [key, item] of Object.entries(value)) {
    if (!DEFECT_FIELDS.has(key) || (item !== null && item !== undefined && typeof item !== "string")) {
      throw new Error("Неподдерживаемые данные замечания. Фото добавляются отдельно.");
    }
    if (typeof item === "string" && DEFECT_TEXT_LIMITS[key] && Array.from(item).length > DEFECT_TEXT_LIMITS[key]) {
      throw new Error(`${DEFECT_FIELD_LABELS[key]}: не более ${DEFECT_TEXT_LIMITS[key]} символов.`);
    }
  }
  if (value.priority && !["low", "normal", "high"].includes(String(value.priority))) throw new Error("Выберите низкий, обычный или высокий приоритет.");
  const day = value.due_date;
  if (day !== undefined && day !== null && day !== "") {
    if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day) || Number(day.slice(0, 4)) < 1) throw new Error("Укажите корректный срок устранения в формате ГГГГ-ММ-ДД.");
    const parsed = new Date(`${day}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== day) throw new Error("Некорректный срок устранения.");
  }
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

function defectEnvelope(command: PreparedDefectCreate) {
  return { client_mutation_id: command.id, device_id: command.deviceId,
    project_id: command.projectId, operation: command.operation,
    client_created_at: command.clientCreatedAt, data: command.patch };
}

export function prepareDefectCreate(args: { userId: number; projectId: number; data: Record<string, unknown> }): PreparedDefectCreate {
  if (!positiveId(args.userId) || !positiveId(args.projectId)) throw new Error("Войдите в учётную запись и выберите проект.");
  const data = checkedDefectData(args.data), device = deviceId();
  if (!/^[A-Za-z0-9_.:-]{8,100}$/.test(device)) throw new Error("Некорректный идентификатор устройства.");
  const now = new Date().toISOString();
  freezeJson(data);
  return Object.freeze({ id: uuid(), userId: args.userId, projectId: args.projectId,
    deviceId: device, operation: "defect.create" as const, patch: data,
    clientCreatedAt: now, status: "queued" as const, attempts: 0, updatedAt: now });
}

export async function enqueuePreparedDefectCreate(command: PreparedDefectCreate): Promise<string> {
  if (command.operation !== "defect.create" || !positiveId(command.userId) || !positiveId(command.projectId)
      || "entityId" in command || "uploads" in command) throw new Error("Некорректное подготовленное замечание.");
  const snapshot = { ...command, patch: checkedDefectData(command.patch) };
  const existing = (await listCommands(snapshot.userId, snapshot.projectId)).find((row) => row.id === snapshot.id);
  if (existing) {
    if (existing.operation !== "defect.create" || JSON.stringify(defectEnvelope(existing)) !== JSON.stringify(defectEnvelope(snapshot))) {
      throw new Error("Идентификатор замечания уже использован с другими данными.");
    }
    return existing.id;
  }
  await persist(snapshot);
  return snapshot.id;
}

export async function enqueueDefectCreate(args: { userId: number; projectId: number; data: Record<string, unknown> }): Promise<string> {
  return enqueuePreparedDefectCreate(prepareDefectCreate(args));
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
  if (!positiveId(args.userId) || !positiveId(args.projectId)) throw new Error("Нужны пользователь и проект для загрузки.");
  if (!args.files.length || args.files.length > 50) throw new Error("Выберите от 1 до 50 файлов.");
  const batches: StoredUploadFile[][] = [];
  let batch: StoredUploadFile[] = [], bytes = 0, selectionBytes = 0;
  for (const file of args.files) {
    const size = file.blob?.size;
    if (!Number.isSafeInteger(size) || size < 0 || size > 4 * 1024 * 1024) throw new Error("Файл больше 4 МБ или повреждён.");
    selectionBytes += size;
    if (selectionBytes > 60 * 1024 * 1024) throw new Error("Общий размер выбранных файлов больше 60 МБ.");
    if (batch.length && (batch.length === 5 || bytes + size > 12 * 1024 * 1024)) {
      batches.push(batch); batch = []; bytes = 0;
    }
    batch.push({ ...file }); bytes += size;
  }
  if (batch.length) batches.push(batch);
  const now = new Date().toISOString();
  const commands: OfflineCommand[] = batches.map(uploads => ({
    id: uuid(), userId: args.userId, projectId: args.projectId, deviceId: deviceId(),
    operation: "document.upload", entityId: 0, patch: {}, uploads,
    clientCreatedAt: now, status: "queued", attempts: 0, updatedAt: now,
  }));
  if (navigator.storage?.persist) await navigator.storage.persist().catch(() => false);
  try { await putCommands(commands); }
  catch (reason) {
    if (reason instanceof DOMException && reason.name === "QuotaExceededError") throw new Error("На устройстве закончилось место. Файлы не сохранены; освободите память и повторите.");
    throw reason;
  }
  changed();
  return commands[0].id;
}

function blobBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Не удалось прочитать сохранённый файл"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(blob);
  });
}

async function submit(command: OfflineCommand): Promise<{ applied: boolean; conflictId?: number; receiptId?: number; entityId?: number; entityNumber?: number }> {
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
  if (command.operation === "defect.create") {
    const response = await api<{
      receipt_id?: unknown; client_mutation_id?: unknown; operation?: unknown;
      status?: unknown; entity_type?: unknown; entity_id?: unknown;
      result?: { id?: unknown; number?: unknown; project_id?: unknown; author_user_id?: unknown };
    }>("/mobile-sync/defect-create", {
      method: "POST", body: JSON.stringify(defectEnvelope(command)),
      headers: { "X-PU-Expected-User": String(command.userId) },
    });
    if (response?.status !== "applied" || response.operation !== "defect.create"
      || response.client_mutation_id !== command.id || response.entity_type !== "defect"
      || !positiveId(response.receipt_id) || !positiveId(response.entity_id) || !positiveId(response.result?.number)
      || response.result?.id !== response.entity_id || response.result?.project_id !== command.projectId
      || response.result?.author_user_id !== command.userId) throw new Error("Не подтверждено создание замечания.");
    return { applied: true, receiptId: response.receipt_id, entityId: response.entity_id, entityNumber: response.result.number };
  }
  if (command.operation === "document.upload") {
    if (!positiveId(command.userId) || !positiveId(command.projectId)) throw new Error("Неверный владелец или проект сохранённой загрузки.");
    const files = await Promise.all((command.uploads || []).map(async (file) => ({
      path: file.path,
      mime_type: file.mimeType,
      content_base64: await blobBase64(file.blob),
    })));
    const response = await api<{ status?: unknown; jobs?: { job_id?: unknown; status?: unknown }[] }>("/local-upload/analyze", {
      method: "POST",
      headers: { "Idempotency-Key": command.id, "X-PU-Expected-User": String(command.userId) },
      body: JSON.stringify({ project_id: command.projectId, files }),
    });
    const acceptedStates = new Set(["queued", "retrying", "running", "already_queued"]);
    if (response?.status !== "queued" || !Array.isArray(response.jobs) || !files.length
      || response.jobs.length !== files.length
      || response.jobs.some(job => !job || !positiveId(job.job_id) || !acceptedStates.has(String(job.status)))
      || new Set(response.jobs.map(job => job.job_id)).size !== files.length) {
      throw new Error("Сервер не подтвердил сохранение всех файлов в защищённой очереди.");
    }
    return { applied: true };
  }
  const response = await api<{
    receipt_id: number;
    client_mutation_id?: unknown;
    user_id?: unknown;
    project_id?: unknown;
    operation?: unknown;
    entity_type?: unknown;
    entity_id?: unknown;
    status: string;
    error_code?: string;
    error_message?: string;
    conflict?: { id: number; command_id?: unknown; entity_type?: unknown; entity_id?: unknown };
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
  const entityType = command.operation === "task.update" ? "task" : "notification";
  if (response?.status === "applied" || response?.status === "conflict") {
    if (!positiveId(response.receipt_id) || response.client_mutation_id !== command.id
      || response.user_id !== command.userId || response.project_id !== command.projectId
      || response.operation !== command.operation || response.entity_type !== entityType
      || response.entity_id !== command.entityId) throw new Error("Не подтверждена доставка сохранённого изменения.");
  }
  if (response?.status === "applied") return { applied: true, receiptId: response.receipt_id };
  if (response.status === "conflict") {
    if (!positiveId(response.conflict?.id) || response.conflict?.command_id !== response.receipt_id
      || response.conflict.entity_type !== entityType || response.conflict.entity_id !== command.entityId) {
      throw new Error("Не подтверждена принадлежность конфликта сохранённому изменению.");
    }
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

export async function submitPreparedDefectCreate(command: PreparedDefectCreate): Promise<{ receiptId: number; entityId: number; entityNumber: number }> {
  if (command.operation !== "defect.create") throw new Error("Ожидалось подготовленное замечание.");
  const result = await submit(command);
  if (!result.applied || !positiveId(result.receiptId) || !positiveId(result.entityId) || !positiveId(result.entityNumber)) throw new Error("Не подтверждено создание замечания.");
  return { receiptId: result.receiptId, entityId: result.entityId, entityNumber: result.entityNumber };
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
          if (working.operation === "defect.create" && positiveId(result.entityId) && positiveId(result.entityNumber)) {
            window.dispatchEvent(new CustomEvent(DEFECT_APPLIED_EVENT, {
              detail: { userId, projectId, defectId: result.entityId, defectNumber: result.entityNumber, clientMutationId: working.id },
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
