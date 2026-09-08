import { expect, it } from "vitest";
import { parseGraph } from "./graphReadModel";
import { planGraphFixture } from "./schedulePlanFixtures";

it("retains the exact server plan without calculating dates or float", () => {
  const raw = planGraphFixture();
  expect(parseGraph(raw, 8)).toHaveProperty("plan", raw.plan);
});

const corruptions: Array<(p: ReturnType<typeof planGraphFixture>["plan"]) => void> = [
  p => { p.critical_ids = [99]; },
  p => { p.tasks.pop(); },
  p => { p.tasks[1].task_id = 12; },
  p => { p.tasks[0].earliest_start = "2026-09-02"; },
  p => { p.tasks[0].latest_finish = "2026-02-30"; },
  p => { p.tasks[0].total_float_days = -1; },
  p => { p.critical_ids = [12]; },
  p => { p.critical_edges = [[13, 12]]; },
  p => { p.critical_edges = [[12, 14]]; },
  p => { p.critical_edges.push([12, 13]); },
  p => { p.topological_order = [12, 13]; },
  p => { p.project_start = "2026-09-02"; },
];
it.each(corruptions)("rejects incomplete, unknown, contradictory or mismatched plan", change => {
  const raw = planGraphFixture(); change(raw.plan);
  expect(() => parseGraph(raw, 8)).toThrow();
});
