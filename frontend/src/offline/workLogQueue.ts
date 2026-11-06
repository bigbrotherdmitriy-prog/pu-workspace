// Offline work-log create journal module: prepare, envelope, ACK validation, submit.
// Not yet wired into the sync engine. No persistence, DOM events or global state.

export type WorkLogCreateData = Record<string, unknown>;

export type WorkLogCreateIdentity = {
  mutationId: string;
  deviceId: string;
  createdAt: string;
};

export type WorkLogCreateInput = {
  userId: number;
  projectId: number;
  data: WorkLogCreateData;
};

export type PreparedWorkLogCreate = {
  readonly id: string;
  readonly userId: number;
  readonly projectId: number;
  readonly deviceId: string;
  readonly operation: "work_log.create";
  readonly data: WorkLogCreateData;
  readonly clientCreatedAt: string;
};

export type WorkLogEnvelope = {
  client_mutation_id: string;
  device_id: string;
  project_id: number;
  operation: "work_log.create";
  client_created_at: string;
  data: WorkLogCreateData;
};

export type WorkLogAck = { receiptId: number; entityId: number };

export type WorkLogTransport = (path: string, init: RequestInit) => Promise<unknown>;

export type WorkLogScopeAssertion = () => void;

const ID_PATTERN = /^[A-Za-z0-9_.:-]+$/;
const DATE_PATTERN = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const ISO_PATTERN = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$/;
const MAX_SAFE = 9007199254740991;
const ACK_PREFIX = "Некорректная квитанция журнала: ";
const PATH = "/mobile-sync/work-log-create";

function fail(message: string): never {
  throw new Error(message);
}

function isPositiveSafeInt(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 && value <= MAX_SAFE;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function isRealCalendarDate(value: string): boolean {
  if (!DATE_PATTERN.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  if (month < 1 || month > 12 || day < 1) return false;
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return day <= daysInMonth;
}

function isIsoWithOffset(value: string): boolean {
  if (!ISO_PATTERN.test(value)) return false;
  return !Number.isNaN(Date.parse(value));
}

function deepCopyJson(value: unknown, seen: Set<object>): unknown {
  if (value === null) return null;
  const kind = typeof value;
  if (kind === "string" || kind === "boolean") return value;
  if (kind === "number") {
    if (!Number.isFinite(value as number)) fail("Данные журнала содержат недопустимое число");
    return value;
  }
  if (kind !== "object") fail("Данные журнала содержат недопустимое значение");
  const obj = value as object;
  if (seen.has(obj)) fail("Данные журнала содержат циклическую ссылку");
  seen.add(obj);
  let copy: unknown;
  if (Array.isArray(obj)) {
    copy = obj.map((item) => deepCopyJson(item, seen));
  } else if (isPlainObject(obj)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(obj)) {
      out[key] = deepCopyJson(obj[key], seen);
    }
    copy = out;
  } else {
    fail("Данные журнала содержат неподдерживаемый объект");
  }
  seen.delete(obj);
  return copy;
}

function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null) {
    for (const key of Object.keys(value as Record<string, unknown>)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
    Object.freeze(value);
  }
  return value;
}

export function prepareWorkLogCreate(
  input: WorkLogCreateInput,
  identity: WorkLogCreateIdentity,
): PreparedWorkLogCreate {
  if (!isPlainObject(input)) fail("Некорректные входные данные журнала");
  const { userId, projectId, data } = input;
  if (!isPositiveSafeInt(userId)) fail("Некорректный идентификатор пользователя");
  if (!isPositiveSafeInt(projectId)) fail("Некорректный идентификатор проекта");
  if (!isPlainObject(data)) fail("Данные журнала должны быть объектом");
  const workDate = data.work_date;
  if (typeof workDate !== "string" || !isRealCalendarDate(workDate)) {
    fail("Некорректная дата работы");
  }
  const workName = data.work_name;
  if (typeof workName !== "string" || workName.trim() === "") {
    fail("Некорректное наименование работы");
  }
  if (!isPlainObject(identity)) fail("Некорректная идентичность мутации");
  const { mutationId, deviceId, createdAt } = identity;
  if (typeof mutationId !== "string" || mutationId.length < 8 || mutationId.length > 90 || !ID_PATTERN.test(mutationId)) {
    fail("Некорректный идентификатор мутации");
  }
  if (typeof deviceId !== "string" || deviceId.length < 8 || deviceId.length > 100 || !ID_PATTERN.test(deviceId)) {
    fail("Некорректный идентификатор устройства");
  }
  if (typeof createdAt !== "string" || !isIsoWithOffset(createdAt)) {
    fail("Некорректная дата создания");
  }
  const copied = deepCopyJson(data, new Set<object>()) as WorkLogCreateData;
  const prepared: PreparedWorkLogCreate = {
    id: mutationId,
    userId,
    projectId,
    deviceId,
    operation: "work_log.create",
    data: copied,
    clientCreatedAt: createdAt,
  };
  return deepFreeze(prepared);
}

export function workLogEnvelope(command: PreparedWorkLogCreate): WorkLogEnvelope {
  return {
    client_mutation_id: command.id,
    device_id: command.deviceId,
    project_id: command.projectId,
    operation: command.operation,
    client_created_at: command.clientCreatedAt,
    data: command.data,
  };
}

function ackFail(message: string): never {
  throw new Error(ACK_PREFIX + message);
}

export function validateWorkLogAck(command: PreparedWorkLogCreate, response: unknown): WorkLogAck {
  if (!isPlainObject(response)) ackFail("ответ не является объектом");
  if (response.status !== "applied") ackFail("статус не applied");
  if (response.operation !== "work_log.create") ackFail("неверная операция");
  if (response.entity_type !== "work_log") ackFail("неверный тип сущности");
  if (response.client_mutation_id !== command.id) ackFail("несовпадение идентификатора мутации");
  const receiptId = response.receipt_id;
  const entityId = response.entity_id;
  if (!isPositiveSafeInt(receiptId)) ackFail("некорректный receipt_id");
  if (!isPositiveSafeInt(entityId)) ackFail("некорректный entity_id");
  const result = response.result;
  if (!isPlainObject(result)) ackFail("результат не является объектом");
  if (!isPositiveSafeInt(result.id) || result.id !== entityId) ackFail("несовпадение id результата");
  if (!isPositiveSafeInt(result.project_id) || result.project_id !== command.projectId) ackFail("несовпадение проекта");
  if (!isPositiveSafeInt(result.author_user_id) || result.author_user_id !== command.userId) ackFail("несовпадение автора");
  return { receiptId, entityId };
}

export async function submitPreparedWorkLogCreate(
  command: PreparedWorkLogCreate,
  transport: WorkLogTransport,
  assertCurrentScope: WorkLogScopeAssertion,
): Promise<WorkLogAck> {
  if (typeof assertCurrentScope !== "function") {
    throw new TypeError("assertCurrentScope должен быть функцией");
  }
  assertCurrentScope();
  const response = await transport(PATH, {
    method: "POST",
    headers: { "X-PU-Expected-User": String(command.userId) },
    body: JSON.stringify(workLogEnvelope(command)),
  });
  assertCurrentScope();
  return validateWorkLogAck(command, response);
}
