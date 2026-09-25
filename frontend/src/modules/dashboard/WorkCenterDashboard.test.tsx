import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { attentionBreakdown, budgetProgress, WorkCenterDashboard, type WorkCenterSummary } from "./WorkCenterDashboard";

const summary: WorkCenterSummary = {
  attention: 15,
  overdue_tasks: 2,
  overdue_obligations: 3,
  open_risks: 4,
  pending_decisions: 1,
  open_obligations: 7,
  unread_notifications: 5,
};

afterEach(cleanup);

function props() {
  return {
    projectName: "Проект №17", summary, budgetActual: 25, budgetPlanned: 100,
    onOpenPlan: vi.fn(), onAskAi: vi.fn(), onOpenDocuments: vi.fn(), onOpenSchedule: vi.fn(), onOpenFinance: vi.fn(),
    onOpenObligations: vi.fn(), onOpenOverdueTasks: vi.fn(), onOpenOverdueObligations: vi.fn(),
    onOpenRisks: vi.fn(), onOpenDecisions: vi.fn(), onOpenNotifications: vi.fn(),
  };
}

describe("WorkCenterDashboard", () => {
  it("keeps the five-row attention breakdown equal to summary.attention", () => {
    const rows = attentionBreakdown(summary);
    expect(rows).toHaveLength(5);
    expect(rows.reduce((total, row) => total + row.value, 0)).toBe(summary.attention);
    render(<WorkCenterDashboard {...props()} />);
    const focus = screen.getByLabelText("Требует решения");
    expect(within(focus).getByText("15")).toBeInTheDocument();
    for (const row of rows) expect(within(focus).getByText(row.label)).toBeInTheDocument();
  });

  it("opens the dedicated filtered register from every attention row", () => {
    const callbacks = props();
    render(<WorkCenterDashboard {...callbacks} />);
    fireEvent.click(screen.getByRole("button", { name: /Просроченные задачи/ }));
    fireEvent.click(screen.getByRole("button", { name: /Просроченные обязательства/ }));
    fireEvent.click(screen.getByRole("button", { name: /Открытые риски/ }));
    fireEvent.click(screen.getByRole("button", { name: /Ждут решения/ }));
    fireEvent.click(screen.getByRole("button", { name: /Непрочитанные уведомления/ }));
    expect(callbacks.onOpenOverdueTasks).toHaveBeenCalledOnce();
    expect(callbacks.onOpenOverdueObligations).toHaveBeenCalledOnce();
    expect(callbacks.onOpenRisks).toHaveBeenCalledOnce();
    expect(callbacks.onOpenDecisions).toHaveBeenCalledOnce();
    expect(callbacks.onOpenNotifications).toHaveBeenCalledOnce();
  });

  it("shows budget actual divided by planned and a dash without a budget", () => {
    expect(budgetProgress(25, 100)).toBe(25);
    expect(budgetProgress(0, 0)).toBeNull();
    const view = render(<WorkCenterDashboard {...props()} />);
    expect(screen.getByText("25%")).toBeInTheDocument();
    view.rerender(<WorkCenterDashboard {...props()} budgetActual={0} budgetPlanned={0} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
