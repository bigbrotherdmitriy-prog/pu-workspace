import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TasksModule } from "./TasksModule";
import { task, members, documents, history, longPath } from "../../../tests/fixtures/tasks";

afterEach(cleanup);
function props(overrides: Partial<ComponentProps<typeof TasksModule>> = {}): ComponentProps<typeof TasksModule> {
  return {
    tasks: [task], filter: "all", members, documents, history,
    completionTaskId: 0, completionNote: "", completionDocumentId: 0, historyTaskId: 0,
    onFilterChange: vi.fn(), onAssign: vi.fn(), onApproveExternal: vi.fn(), onUpdate: vi.fn(),
    onStartCompletion: vi.fn(), onCancelCompletion: vi.fn(), onCompletionNoteChange: vi.fn(),
    onCompletionDocumentChange: vi.fn(), onLoadHistory: vi.fn(), ...overrides,
  };
}

describe("TasksModule layout and existing actions", () => {
  it("preserves full Russian content and groups controls separately from the body", () => {
    const { container } = render(<TasksModule {...props()} />);
    expect(screen.getByText(task.title)).toBeInTheDocument();
    expect(container.querySelector(".task-body p")).toHaveTextContent(`${longPath} · ${task.assignee_name} · эвристическая оценка 42/100`);
    expect(screen.getByText(task.source_excerpt)).toBeInTheDocument();
    expect(container.querySelector(".task-action-buttons")?.querySelectorAll("button")).toHaveLength(4);
    expect(container.querySelector(".task-body select")).toBeNull();
  });

  it("shows backend review reasons as plain text without treating the score as probability", () => {
    const description = "Причины: есть символы замены. <script>synthetic</script>";
    const callbacks = props({ tasks: [{ ...task, description, confidence: 0.45 }] });
    const { container } = render(<TasksModule {...callbacks} />);
    expect(screen.getByText(description)).toBeInTheDocument();
    expect(screen.getByText("Требуется ручная проверка по документу-источнику.")).toBeInTheDocument();
    expect(screen.getByText(/не является вероятностью/)).toBeInTheDocument();
    expect(container.querySelector(".task-description script")).toBeNull();
    expect(callbacks.onApproveExternal).not.toHaveBeenCalled();
  });

  it.each([null, undefined])("supports historical rows without description (%s)", (description) => {
    const { container } = render(<TasksModule {...props({ tasks: [{ ...task, description, confidence: 0.82, needs_review: false }] })} />);
    expect(container.querySelector(".task-description")).toBeNull();
    expect(container.querySelector(".task-review-warning")).toBeNull();
    expect(screen.getByText(/эвристическая оценка 82\/100/)).toBeInTheDocument();
    expect(screen.getByText(/Отсутствие предупреждений не гарантирует/)).toBeInTheDocument();
  });

  it("retains assignment, external approval, start, completion request and history callbacks", () => {
    const callbacks = props();
    render(<TasksModule {...callbacks} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Поставить задачу" }));
    fireEvent.click(screen.getByRole("button", { name: "В работу" }));
    fireEvent.click(screen.getByRole("button", { name: /^Завершить$/ }));
    fireEvent.click(screen.getByRole("button", { name: "История" }));
    expect(callbacks.onAssign).toHaveBeenCalledWith(task, 2);
    expect(callbacks.onApproveExternal).toHaveBeenCalledWith(task);
    expect(callbacks.onUpdate).toHaveBeenCalledExactlyOnceWith(task, "in_progress");
    expect(callbacks.onStartCompletion).toHaveBeenCalledWith(task);
    expect(callbacks.onLoadHistory).toHaveBeenCalledWith(task);
  });

  it("keeps completion confirmation separate and requires a nonblank result", () => {
    const callbacks = props({ completionTaskId: task.id, completionNote: "   " });
    const { rerender } = render(<TasksModule {...callbacks} />);
    const confirm = screen.getByRole("button", { name: "Подтвердить завершение" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(callbacks.onUpdate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Акт подготовлен" } });
    expect(callbacks.onCompletionNoteChange).toHaveBeenCalledWith("Акт подготовлен");
    fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "7" } });
    expect(callbacks.onCompletionDocumentChange).toHaveBeenCalledWith(7);
    rerender(<TasksModule {...callbacks} completionNote="Акт подготовлен" completionDocumentId={7} />);
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(callbacks.onUpdate).toHaveBeenCalledExactlyOnceWith(task, "completed");
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(callbacks.onCancelCompletion).toHaveBeenCalledOnce();
  });

  it("shows full history alongside completion without losing evidence text", () => {
    const { container } = render(<TasksModule {...props({ completionTaskId: 1, historyTaskId: 1 })} />);
    expect(screen.getByText("Подтверждение выполнения")).toBeInTheDocument();
    expect(screen.getByText("История задачи и решений")).toBeInTheDocument();
    expect(container.querySelector(".task-history")).toHaveTextContent(history[0].result_note!);
    expect(screen.getByText(`Подтверждение: ${longPath}`)).toBeInTheDocument();
  });

  it.each([
    ["open", [1, 2]], ["overdue", [1]], ["review", [1]], ["all", [1, 2, 3]],
  ])("preserves %s filtering", (filter, ids) => {
    const tasks = [task,
      { ...task, id: 2, title: "Будущая задача", due_date: "2999-01-01", needs_review: false, status: "in_progress" },
      { ...task, id: 3, title: "Выполненная задача", needs_review: false, status: "completed" },
    ];
    render(<TasksModule {...props({ tasks, filter: String(filter) })} />);
    for (const item of tasks) expect(Boolean(screen.queryByText(item.title))).toBe((ids as number[]).includes(item.id));
  });

  it("retains all filter callbacks and the empty state", () => {
    const callbacks = props({ tasks: [] });
    render(<TasksModule {...callbacks} />);
    for (const [label, value] of [["Открытые", "open"], ["Просроченные", "overdue"], ["На проверку", "review"], ["Все", "all"]]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      expect(callbacks.onFilterChange).toHaveBeenLastCalledWith(value);
    }
    expect(screen.getByText("Задач в этом фильтре нет")).toBeInTheDocument();
  });

  it("does not reintroduce completion or publication actions for an executed completed task", () => {
    render(<TasksModule {...props({ tasks: [{ ...task, status: "completed", external_action_status: "executed" }] })} />);
    expect(screen.queryByRole("button", { name: /^Завершить$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Поставить задачу" })).toBeNull();
    expect(screen.getByRole("button", { name: "История" })).toBeInTheDocument();
  });
});
