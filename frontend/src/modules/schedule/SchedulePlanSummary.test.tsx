import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseGraph, type Graph } from "./graphReadModel";
import { SchedulePlanSummary } from "./SchedulePlanSummary";
import { ScheduleGraphEditor } from "./ScheduleGraphEditor";
import { planGraphFixture } from "./schedulePlanFixtures";
afterEach(cleanup);

it("shows exact saved critical IDs, edges and both float fields without controls", () => {
  render(<SchedulePlanSummary graph={parseGraph(planGraphFixture(), 8)} />);
  expect(screen.getByText(/Сохранённая версия 2, ревизия графа 3/)).toBeInTheDocument();
  expect(screen.getByText(/Утверждённая версия/)).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Критические работы" })).getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByRole("list", { name: "Критические связи" })).toHaveTextContent("(#12) → Synthetic installation (#13)");
  const row = screen.getByRole("row", { name: /Synthetic independent work/ });
  expect(within(row).getAllByRole("cell").slice(-2).map(cell => cell.textContent)).toEqual(["2", "2"]);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("does not fabricate criticality for unavailable plans", () => {
  render(<SchedulePlanSummary graph={parseGraph({ ...planGraphFixture(), plan: null }, 8)} />);
  expect(screen.getByRole("status")).toHaveTextContent("Критичность и резервы не определены");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("hides metrics for stale scope and malformed plan passed directly", () => {
  const view = render(<SchedulePlanSummary graph={parseGraph(planGraphFixture(), 8)} stale />);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  const graph = parseGraph(planGraphFixture(), 8);
  view.rerender(<SchedulePlanSummary graph={{ ...graph, plan: { ...graph.plan, critical_ids: [99] } } as Graph} />);
  expect(screen.getByRole("status")).toHaveTextContent("неполон");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("labels local edits as excluded from saved metrics and escapes titles", () => {
  const fixture = planGraphFixture(); fixture.items[0].title = "<script>synthetic</script>";
  const { container } = render(<SchedulePlanSummary graph={parseGraph(fixture, 8)} hasLocalEdits />);
  expect(screen.getByRole("status")).toHaveTextContent("Локальные правки не включены");
  expect(container.querySelector("script")).toBeNull();
});

it("supports an empty server plan without inventing a finish date", () => {
  const raw = planGraphFixture();
  render(<SchedulePlanSummary graph={parseGraph({ ...raw, items: [], plan: { ...raw.plan, tasks: [],
    critical_ids: [], critical_edges: [], topological_order: [], project_finish: null } }, 8)} />);
  expect(screen.getByText(/Горизонт расчёта/)).toHaveTextContent("нет этапов");
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("discards a late plan after switching the editor project", async () => {
  let finish!: (value: unknown) => void;
  const api = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ ...planGraphFixture(), plan: null });
  const view = render(<ScheduleGraphEditor projectId={1} baselineId={8} api={api} canEdit={false} />);
  view.rerender(<ScheduleGraphEditor projectId={2} baselineId={8} api={api} canEdit={false} />);
  await act(async () => { finish(planGraphFixture()); });
  expect(screen.queryByText("Критические работы и резервы")).not.toBeInTheDocument();
  expect(api.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

it("rejects an older saved calculation on reload without rendering its metrics", async () => {
  const api = vi.fn().mockResolvedValueOnce(planGraphFixture()).mockResolvedValueOnce({ ...planGraphFixture(), graph_revision: 2 });
  render(<ScheduleGraphEditor projectId={1} baselineId={8} api={api} canEdit={false} />);
  await screen.findByText("Критические работы и резервы");
  fireEvent.click(screen.getByText("Обновить серверную версию"));
  await screen.findByText(/Не удалось обновить ГПР/);
  expect(screen.queryByText("Критические работы и резервы")).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(2);
});
