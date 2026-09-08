import { describe, expect, it } from "vitest";
import { parseGraph, putPayload, sameItems, toDraft, validDate } from "./graphReadModel";

export const graphFixture = (overrides: Record<string, unknown> = {}) => ({
  baseline_id: 8, version: 2, status: "draft", graph_revision: 3, planning_mode: "calendar_graph", project_start: "2026-09-01",
  items: [{ id: 12, title: "Подготовка", duration_days: 2, is_milestone: false, predecessor_ids: null,
    constraint_type: "asap", constraint_date: null, not_before_date: null, planned_start: "2026-09-01", planned_finish: "2026-09-02" }],
  plan: null, ...overrides,
});
describe("persisted graph read/write contract", () => {
  it("sends only exact accepted intent fields and graph revision, never display version/dates/title/plan", () => {
    const g = parseGraph(graphFixture(), 8);
    expect(putPayload(toDraft(g), g)).toEqual({ expected_graph_revision: 3, project_start: "2026-09-01", items: [{
      id: 12, duration_days: 2, is_milestone: false, predecessor_ids: null, constraint_type: "asap", constraint_date: null, not_before_date: null,
    }] });
  });
  it("does not fabricate intent or today anchor for legacy graph", () => {
    const source = graphFixture(); source.items[0] = { ...source.items[0], duration_days: null, is_milestone: null, constraint_type: null } as unknown as typeof source.items[0];
    const g = parseGraph({ ...source, project_start: null, planning_mode: "legacy_dates" }, 8);
    const draft = toDraft(g); expect(draft.anchor).toBe(""); expect(draft.items[0].duration).toBe("");
    expect(() => putPayload(draft, g)).toThrow();
  });
  it.each(["2026-02-30", "0000-01-01", "2026-9-01", "tomorrow", "2026-01-01T00:00:00Z"])("rejects invalid date %s", value => expect(validDate(value)).toBe(false));
  it("accepts leap day", () => expect(validDate("2024-02-29")).toBe(true));
  it.each([{ baseline_id: 9 }, { graph_revision: Number.MAX_SAFE_INTEGER + 1 }, { graph_revision: 0 }, { items: [{ id: 1 }] }, { project_start: "2026-02-30" }])("rejects malformed/scope response %j", patch => expect(() => parseGraph(graphFixture(patch), 8)).toThrow());
  it("rejects duplicate IDs", () => { const g = graphFixture(); expect(() => parseGraph({ ...g, items: [g.items[0], g.items[0]] }, 8)).toThrow(); });
  it.each(["", "1.5", "1e2", "-1", "10001", "NaN", "0"])("rejects non-work duration %s", duration => {
    const g = parseGraph(graphFixture(), 8); const d = toDraft(g); d.items[0].duration = duration;
    expect(() => putPayload(d, g)).toThrow();
  });
  it("supports zero milestone and explicit constraints with not-before", () => {
    const g = parseGraph(graphFixture(), 8); const d = toDraft(g);
    d.items[0] = { ...d.items[0], milestone: true, duration: "0", constraint: "mso", constraintDate: "2026-09-04", notBefore: "2026-09-02", dependencies: " 7FS+2d; 9SS-1d " };
    const item = putPayload(d, g).items[0]; expect(item.is_milestone).toBe(true); expect(item.predecessor_ids).toBe("7FS+2d; 9SS-1d"); expect(item.constraint_date).toBe("2026-09-04");
  });
  it.each(["approved", "superseded", "unknown"])("never mutates %s", status => { const g = parseGraph(graphFixture({ status }), 8); expect(() => putPayload(toDraft(g), g)).toThrow(); });
  it("refuses partial graph and changed item set", () => { const g = parseGraph(graphFixture(), 8); const d = toDraft(g); d.items = []; expect(sameItems(d, g)).toBe(false); expect(() => putPayload(d, g)).toThrow(); });
  it("requires non-asap date and forbids leftover asap date", () => {
    const g = parseGraph(graphFixture(), 8); const d = toDraft(g);
    d.items[0].constraint = "mfo"; expect(() => putPayload(d, g)).toThrow();
    d.items[0].constraint = "asap"; d.items[0].constraintDate = "2026-09-02"; expect(() => putPayload(d, g)).toThrow();
  });
});
