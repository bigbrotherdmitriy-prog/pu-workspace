import { describe, it, expect, vi } from "vitest";
import {
  prepareWorkLogCreate,
  workLogEnvelope,
  validateWorkLogAck,
  submitPreparedWorkLogCreate,
} from "./workLogQueue";

type Cmd = ReturnType<typeof prepareWorkLogCreate>;

const identity = {
  mutationId: "synthetic-mutation-1",
  deviceId: "synthetic-device-1",
  createdAt: "2024-05-01T10:00:00.000Z",
};

const baselineData = { work_date: "2026-11-06", work_name: "SYNTHETIC", note: "hi" };

function makeCmd(over: Record<string, unknown> = {}, idOver: Partial<typeof identity> = {}): Cmd {
  return prepareWorkLogCreate(
    { userId: 7, projectId: 3, data: { ...baselineData, ...over } },
    { ...identity, ...idOver },
  );
}

function validAck(cmd: Cmd) {
  return {
    receipt_id: 10,
    client_mutation_id: cmd.id,
    operation: "work_log.create",
    entity_type: "work_log",
    entity_id: 20,
    status: "applied",
    result: { id: 20, project_id: cmd.projectId, author_user_id: cmd.userId },
  };
}

describe("prepareWorkLogCreate", () => {
  it("accepts a valid baseline command", () => {
    const cmd = makeCmd();
    expect(cmd.data).toMatchObject(baselineData);
    expect(cmd.id).toBe("synthetic-mutation-1");
    expect(cmd.deviceId).toBe("synthetic-device-1");
    expect(cmd.operation).toBe("work_log.create");
    expect(cmd.userId).toBe(7);
    expect(cmd.projectId).toBe(3);
  });

  it("deep-copies nested objects and arrays", () => {
    const nested = { a: { b: [1, 2, { c: 3 }] } };
    const cmd = makeCmd({ nested });
    nested.a.b.push(99);
    (nested.a.b[2] as { c: number }).c = 42;
    expect((cmd.data as any).nested).toEqual({ a: { b: [1, 2, { c: 3 }] } });
  });

  it("is frozen and identity is stable", () => {
    const cmd = makeCmd();
    expect(Object.isFrozen(cmd)).toBe(true);
    expect(cmd.id).toBe("synthetic-mutation-1");
    expect(cmd.deviceId).toBe("synthetic-device-1");
    expect(cmd.operation).toBe("work_log.create");
    expect(cmd.clientCreatedAt).toBe(identity.createdAt);
    expect(cmd.userId).toBe(7);
    expect(cmd.projectId).toBe(3);
  });

  it("rejects invalid calendar dates in createdAt", () => {
    for (const d of ["0000-01-01T00:00:00.000Z", "2024-02-30T00:00:00.000Z", "1900-02-29T00:00:00.000Z"]) {
      expect(() => makeCmd({}, { createdAt: d })).toThrow();
    }
  });

  it("accepts leap-year edge dates in createdAt", () => {
    expect(() => makeCmd({}, { createdAt: "0004-02-29T00:00:00.000Z" })).not.toThrow();
    expect(() => makeCmd({}, { createdAt: "2000-02-29T00:00:00.000Z" })).not.toThrow();
  });

  it("rejects invalid work_date calendar dates", () => {
    for (const d of ["0000-01-01", "2026-02-30", "1900-02-29"]) {
      expect(() => makeCmd({ work_date: d })).toThrow();
    }
  });

  it("accepts leap-year edge work_date dates", () => {
    expect(() => makeCmd({ work_date: "0004-02-29" })).not.toThrow();
    expect(() => makeCmd({ work_date: "2000-02-29" })).not.toThrow();
  });

  it("rejects invalid createdAt date/time/offset", () => {
    for (const d of ["not-a-date", "2024-05-01", "2024-05-01T25:00:00.000Z", "2024-05-01T10:00:00.000+99:00"]) {
      expect(() => makeCmd({}, { createdAt: d })).toThrow();
    }
  });

  it("rejects NaN, Infinity, cycles and Date objects", () => {
    expect(() => makeCmd({ x: NaN })).toThrow();
    expect(() => makeCmd({ x: Infinity })).toThrow();
    const cyc: Record<string, unknown> = {};
    cyc.self = cyc;
    expect(() => makeCmd({ x: cyc })).toThrow();
    expect(() => makeCmd({ x: new Date() })).toThrow();
  });

  it("retains own __proto__/constructor/prototype keys without prototype pollution", () => {
    const raw = '{"__proto__":{"p":1},"constructor":{"c":2},"prototype":{"t":3}}';
    const parsed = JSON.parse(raw);
    const cmd = makeCmd({ parsed });
    const out = (cmd.data as any).parsed;
    expect(Object.keys(out).sort()).toEqual(["__proto__", "constructor", "prototype"]);
    expect(Object.getPrototypeOf(out)).toBe(Object.prototype);
    expect(({} as any).p).toBeUndefined();
    expect(JSON.parse(JSON.stringify(out))).toEqual(JSON.parse(raw));
  });
});

describe("workLogEnvelope", () => {
  it("has exactly six keys and no actor/entity extras", () => {
    const env = workLogEnvelope(makeCmd());
    expect(Object.keys(env).sort()).toEqual(
      ["client_created_at", "client_mutation_id", "data", "device_id", "operation", "project_id"].sort(),
    );
    expect(env).not.toHaveProperty("actor");
    expect(env).not.toHaveProperty("entity");
  });
});

describe("validateWorkLogAck", () => {
  it("accepts a valid ACK", () => {
    const cmd = makeCmd();
    expect(validateWorkLogAck(cmd, validAck(cmd))).toEqual({ receiptId: 10, entityId: 20 });
  });

  const bad: Array<[string, (a: any, c: Cmd) => void]> = [
    ["wrong mutation", (a) => (a.client_mutation_id = "other")],
    ["wrong operation", (a) => (a.operation = "work_log.update")],
    ["wrong status", (a) => (a.status = "rejected")],
    ["wrong entity type", (a) => (a.entity_type = "project")],
    ["bool receipt", (a) => (a.receipt_id = true)],
    ["fraction receipt", (a) => (a.receipt_id = 1.5)],
    ["unsafe receipt", (a) => (a.receipt_id = Number.MAX_SAFE_INTEGER + 2)],
    ["bool entity", (a) => (a.entity_id = false)],
    ["fraction entity", (a) => (a.entity_id = 2.5)],
    ["unsafe entity", (a) => (a.entity_id = Number.MAX_SAFE_INTEGER + 2)],
    ["result id mismatch", (a) => (a.result.id = 21)],
    ["result project mismatch", (a) => (a.result.project_id = 999)],
    ["result author mismatch", (a) => (a.result.author_user_id = 999)],
  ];

  it.each(bad)("rejects %s", (_name, mutate) => {
    const cmd = makeCmd();
    const ack = validAck(cmd);
    mutate(ack, cmd);
    expect(() => validateWorkLogAck(cmd, ack)).toThrow();
  });
});

describe("submitPreparedWorkLogCreate", () => {
  it("POSTs exact envelope to mobile-sync path with expected-user header", async () => {
    const cmd = makeCmd();
    const transport = vi.fn(async () => validAck(cmd));
    const assertScope = vi.fn();
    const res = await submitPreparedWorkLogCreate(cmd, transport, assertScope);
    expect(res).toEqual({ receiptId: 10, entityId: 20 });
    expect(transport).toHaveBeenCalledTimes(1);
    const [path, init] = transport.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/mobile-sync/work-log-create");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-PU-Expected-User"]).toBe(String(cmd.userId));
    expect(JSON.parse(init.body as string)).toEqual(workLogEnvelope(cmd));
    expect(assertScope).toHaveBeenCalled();
  });

  it("checks scope before and after the call", async () => {
    const cmd = makeCmd();
    const order: string[] = [];
    const transport = vi.fn(async () => {
      order.push("transport");
      return validAck(cmd);
    });
    const assertScope = vi.fn(() => order.push("scope"));
    await submitPreparedWorkLogCreate(cmd, transport, assertScope);
    expect(order).toEqual(["scope", "transport", "scope"]);
  });

  it("does not POST when the first guard throws", async () => {
    const cmd = makeCmd();
    const transport = vi.fn(async () => validAck(cmd));
    const assertScope = vi.fn(() => {
      throw new Error("scope changed");
    });
    await expect(submitPreparedWorkLogCreate(cmd, transport, assertScope)).rejects.toThrow("scope changed");
    expect(transport).not.toHaveBeenCalled();
  });

  it("rejects a late ACK when scope changed during the request", async () => {
    const cmd = makeCmd();
    let release!: (v: unknown) => void;
    const transport = vi.fn(() => new Promise<unknown>((r) => (release = r)));
    let calls = 0;
    const assertScope = vi.fn(() => {
      calls += 1;
      if (calls === 2) throw new Error("scope changed");
    });
    const p = submitPreparedWorkLogCreate(cmd, transport, assertScope);
    release(validAck(cmd));
    await expect(p).rejects.toThrow("scope changed");
  });

  it("rethrows transport rejection", async () => {
    const cmd = makeCmd();
    const transport = vi.fn(async () => {
      throw new Error("network");
    });
    await expect(submitPreparedWorkLogCreate(cmd, transport, vi.fn())).rejects.toThrow("network");
  });

  it("rejects an invalid ACK", async () => {
    const cmd = makeCmd();
    const transport = vi.fn(async () => ({ ...validAck(cmd), status: "rejected" }));
    await expect(submitPreparedWorkLogCreate(cmd, transport, vi.fn())).rejects.toThrow();
  });
});
