import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GprWorkspace } from "./GprWorkspace";
import type { FinanceOverview } from "./types";

const finance = {
  baselines: [
    { id: 10, contract_id: 4, name: "ГПР 02.09.2026", version: 2, status: "draft" },
    { id: 9, contract_id: 4, name: "Утверждённый ГПР", version: 1, status: "approved" },
  ],
  schedule: [
    { id: 1, baseline_id: 10, title: "Подготовка", sort_order: 1, duration_days: 3, planned_start: "2026-09-01", planned_finish: "2026-09-03", planned_progress: 100, actual_progress: 100, status: "completed" },
    { id: 2, baseline_id: 10, parent_id: 1, title: "Монтаж", sort_order: 2, duration_days: 7, predecessor_ids: "1", planned_start: "2026-09-04", planned_finish: "2026-09-10", planned_progress: 50, actual_progress: 20, status: "in_progress" },
    { id: 101, baseline_id: 9, title: "Подготовка", sort_order: 1, duration_days: 3, planned_start: "2026-09-01", planned_finish: "2026-09-03", planned_progress: 100, actual_progress: 0, status: "planned" },
    { id: 102, baseline_id: 9, parent_id: 101, title: "Монтаж", sort_order: 2, duration_days: 6, predecessor_ids: "101", planned_start: "2026-09-04", planned_finish: "2026-09-09", planned_progress: 50, actual_progress: 0, status: "planned" },
  ],
} as FinanceOverview;

describe("GprWorkspace", () => {
  it("shows the task grid and saves edits to the selected task", async () => {
    const onUpdateTask = vi.fn().mockResolvedValue(undefined);
    render(<GprWorkspace projectId={1} finance={finance} selectedContractId={4} onPrepare={vi.fn()} onUpdateTask={onUpdateTask} onBulkUpdate={vi.fn().mockResolvedValue(undefined)} onCloneBaseline={vi.fn().mockResolvedValue(11)} onImported={vi.fn()} />);

    expect(screen.getByText("Монтаж")).toBeInTheDocument();
    expect(screen.getByText("Критический путь")).toBeInTheDocument();
    expect(screen.getByLabelText("Связи задач")).toBeInTheDocument();
    expect(screen.getAllByText("0 дн.").length).toBeGreaterThan(0);
    expect(screen.getByText("+1 дн.")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Свернуть ветвь"));
    expect(screen.queryByText("Монтаж")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Развернуть ветвь"));
    fireEvent.click(screen.getByText("Монтаж"));
    const title = await screen.findByLabelText("Название задачи");
    fireEvent.change(title, { target: { value: "Монтаж ИБП" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(onUpdateTask).toHaveBeenCalledWith(2, expect.objectContaining({ title: "Монтаж ИБП" })));
  });
});
