import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OverdueMetric } from "./OverdueMetric";
afterEach(cleanup);

describe("OverdueMetric", () => {
  it("explains that the total includes both tasks and obligations", () => {
    render(<OverdueMetric tasks={13} obligations={13} onOpenTasks={vi.fn()} />);
    expect(screen.getByText("26")).toBeInTheDocument();
    expect(screen.getByText("Задачи: 13 · обязательства: 13")).toBeInTheDocument();
  });

  it("labels the navigation as tasks rather than all overdue items", () => {
    const open = vi.fn();
    render(<OverdueMetric tasks={2} obligations={5} onOpenTasks={open} />);
    fireEvent.click(screen.getByRole("button", {
      name: "Просрочено: задач 2, обязательств 5. Открыть просроченные задачи",
    }));
    expect(open).toHaveBeenCalledOnce();
  });

  it("shows a clear empty state", () => {
    render(<OverdueMetric tasks={0} obligations={0} onOpenTasks={vi.fn()} />);
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("Задачи: 0 · обязательства: 0")).toBeInTheDocument();
  });
});
