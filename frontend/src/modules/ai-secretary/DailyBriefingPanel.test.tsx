import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DailyBriefingPanel, type DailyBriefing } from "./DailyBriefingPanel";

function briefing(): DailyBriefing {
  return {
    project_id: 1,
    date: "2026-09-04",
    summary: {
      attention: 10,
      overdue_tasks: 10,
      overdue_obligations: 0,
      open_risks: 0,
      pending_decisions: 0,
      drafts_waiting_approval: 0,
      messages_waiting_context: 0,
    },
    attention: Array.from({ length: 10 }, (_, index) => ({
      kind: "overdue_task" as const,
      entity_id: index + 1,
      priority: "critical" as const,
      title: `Задача ${index + 1}`,
      next_step: "Проверить",
    })),
    next_step: "Проверить задачи",
    external_actions_created: false,
  };
}

describe("DailyBriefingPanel", () => {
  it("shows a short action list and expands it only on request", () => {
    render(<DailyBriefingPanel briefing={briefing()} onOpenSection={vi.fn()} />);

    expect(screen.getByText("Задача 8")).toBeInTheDocument();
    expect(screen.queryByText("Задача 9")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 2" }));
    expect(screen.getByText("Задача 10")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свернуть список" })).toBeInTheDocument();
  });

  it("opens the execution section for a grouped empty schedule warning", () => {
    const onOpenSection = vi.fn();
    const data = briefing();
    data.attention = [{
      kind: "empty_schedule", entity_id: 11, priority: "high",
      title: "В 6 ГПР нет этапов", next_step: "Добавить этапы",
    }];
    const { container } = render(<DailyBriefingPanel briefing={data} onOpenSection={onOpenSection} />);

    fireEvent.click(within(container).getByRole("button", { name: "Открыть" }));

    expect(onOpenSection).toHaveBeenCalledWith("Исполнение и финансы");
  });
});
