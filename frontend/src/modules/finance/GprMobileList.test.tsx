import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GPR_MOBILE_INDENT, GprMobileList } from "./GprMobileList";
import type { FinanceOverview } from "./types";

type Task = FinanceOverview["schedule"][number];
afterEach(cleanup);

const tasks: Task[] = [
  { id: 1, baseline_id: 10, title: "Подготовка", planned_start: "2026-09-01", planned_finish: "2026-09-03", planned_progress: 100, actual_progress: 100, status: "completed" },
  { id: 2, baseline_id: 10, parent_id: 1, title: "Монтаж фасада", planned_start: "2026-09-04", planned_finish: "2026-09-10", planned_progress: 50, actual_progress: 20, status: "in_progress" },
  { id: 3, baseline_id: 10, parent_id: 2, title: "Сдача секции", is_milestone: true, planned_finish: "2026-09-10", planned_progress: 0, actual_progress: 0, status: "planned" },
];
const depth = new Map([[1, 0], [2, 1], [3, 2]]);

function setup(overrides: Partial<Parameters<typeof GprMobileList>[0]> = {}) {
  const props = {
    tasks, numberOf: (task: Task) => task.id, depthOf: (task: Task) => depth.get(task.id) || 0,
    parents: new Set([1, 2]), collapsedIds: new Set<number>(), critical: new Set([2, 3]), showCritical: true,
    selectedId: 2, onSelect: vi.fn(), onToggle: vi.fn(), ...overrides,
  };
  render(<GprMobileList {...props} />);
  return props;
}

describe("GprMobileList", () => {
  it("shows the hierarchy with a small indent, dates, progress and the critical path", () => {
    setup();
    const items = within(screen.getByRole("list", { name: "Этапы и вехи ГПР" })).getAllByRole("listitem");
    expect(items.map((item) => item.style.paddingLeft)).toEqual(["0px", `${GPR_MOBILE_INDENT}px`, `${GPR_MOBILE_INDENT * 2}px`]);
    expect(GPR_MOBILE_INDENT).toBeGreaterThanOrEqual(12);
    expect(GPR_MOBILE_INDENT).toBeLessThanOrEqual(16);
    expect(items[1]).toHaveTextContent("04.09.26 — 10.09.26");
    expect(within(items[1]).getByLabelText("Выполнено 20%")).toHaveTextContent("20%");
    expect(within(items[1]).getByText("Критический путь")).toBeInTheDocument();
    expect(within(items[0]).queryByText("Критический путь")).toBeNull();
    expect(items[2]).toHaveTextContent("Веха · 10.09.26");
    expect(within(items[2]).getByLabelText("Веха")).toBeInTheDocument();
    expect(items[1].querySelector(".gpr-mobile-task")).toHaveAttribute("aria-pressed", "true");
    expect(items[0].querySelector(".gpr-mobile-task")).toHaveAttribute("aria-pressed", "false");
  });

  it("hides the critical mark when the critical path is switched off", () => {
    setup({ showCritical: false });
    expect(screen.queryByText("Критический путь")).toBeNull();
  });

  it("selects a task and toggles a branch", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /^1\s*Подготовка/ }));
    expect(props.onSelect).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByRole("button", { name: "Свернуть Монтаж фасада" }));
    expect(props.onToggle).toHaveBeenCalledWith(2);
  });

  it("offers to expand a collapsed branch", () => {
    setup({ collapsedIds: new Set([1]), tasks: [tasks[0]] });
    expect(screen.getByRole("button", { name: "Развернуть Подготовка" })).toHaveAttribute("aria-expanded", "false");
  });
});
