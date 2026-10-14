import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BacklogTriagePanel, pluralRu } from "./BacklogTriagePanel";
import { TasksModule, isMyActiveTask } from "./TasksModule";
import { storedTaskFilter, rememberTaskFilter, TASK_FILTER_STORAGE_KEY } from "./taskFilterStorage";
import { task, members, documents, history } from "../../../tests/fixtures/tasks";

type Call = { url: string; body: any };
let calls: Call[] = [];
let responses: Array<{ status?: number; body: unknown }> = [];

function respond(body: unknown, status = 200) { responses.push({ body, status }); }

beforeEach(() => {
  calls = []; responses = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const next = responses.shift() || { body: {}, status: 200 };
    return new Response(JSON.stringify(next.body), {
      status: next.status, headers: { "Content-Type": "application/json" },
    });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

function panel(overrides: Partial<ComponentProps<typeof BacklogTriagePanel>> = {}) {
  const props = { kind: "tasks" as const, projectId: 17, onDone: vi.fn(), ...overrides };
  render(<BacklogTriagePanel {...props} />);
  return props;
}

describe("BacklogTriagePanel", () => {
  it("previews the default preset, confirms with the exact count and offers undo", async () => {
    const props = panel();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    expect(screen.getByLabelText("Автоизвлечённые старше 30 дней без изменений")).toBeChecked();

    respond({ dry_run: true, count: 25, sample: [{ id: 1, title: "Оплатить счёт", status: "assigned" }] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    expect(await screen.findByText("Найдено: 25")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Примеры" })).getByText("Оплатить счёт")).toBeInTheDocument();
    expect(screen.getByText("и ещё 24")).toBeInTheDocument();
    expect(calls[0].url).toBe("/projects/17/tasks/bulk-close");
    expect(calls[0].body).toEqual({
      filter: { older_than_days: 30, untouched_only: true, auto_extracted_only: true }, dry_run: true, reason: "",
    });

    respond({ dry_run: false, batch_id: 501, count: 25 });
    fireEvent.click(screen.getByRole("button", { name: "Закрыть 25 задач (можно отменить)" }));
    expect(await screen.findByText("Закрыто: 25 задач.")).toBeInTheDocument();
    expect(calls[1].body).toMatchObject({ dry_run: false, expected_count: 25 });
    expect(calls[1].body.reason).toMatch(/^Разбор очереди/);
    expect(props.onDone).toHaveBeenCalledTimes(1);

    respond({ batch_id: 501, restored: 25, skipped: [] });
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    expect(await screen.findByText(/Восстановлено: 25/)).toBeInTheDocument();
    expect(calls[2].url).toBe("/projects/17/tasks/bulk-close/501/undo");
    expect(props.onDone).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Отменить" })).toBeNull();
  });

  it("uses the 60-day preset and drops a stale preview when the preset changes", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    respond({ dry_run: true, count: 3, sample: [] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    expect(await screen.findByText("Найдено: 3")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Все старше 60 дней без изменений"));
    expect(screen.queryByText("Найдено: 3")).toBeNull();
    respond({ dry_run: true, count: 0, sample: [] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    expect(await screen.findByText("Найдено: 0")).toBeInTheDocument();
    expect(calls[1].body.filter).toEqual({ older_than_days: 60, untouched_only: true });
    expect(screen.queryByRole("button", { name: /можно отменить/ })).toBeNull();
  });

  it("lets the owner lower the age threshold of a preset", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    fireEvent.change(screen.getByLabelText("Старше, дней"), { target: { value: "21" } });
    expect(screen.getByLabelText("Автоизвлечённые старше 21 дня без изменений")).toBeChecked();
    respond({ dry_run: true, count: 337, sample: [] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    await screen.findByText("Найдено: 337");
    expect(calls[0].body.filter).toEqual({ older_than_days: 21, untouched_only: true, auto_extracted_only: true });
    fireEvent.click(screen.getByLabelText("Все старше 60 дней без изменений"));
    expect(screen.getByLabelText("Старше, дней")).toHaveValue(60);
  });

  it("dismisses drafts through the drafts endpoint with draft wording", async () => {
    panel({ kind: "drafts", selectedIds: [7, 9] });
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    fireEvent.click(screen.getByLabelText("Выбранные вручную галочками (2)"));
    respond({ dry_run: true, count: 2, sample: [{ id: 7, title: "Re: письмо", status: "draft" }] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    await screen.findByText("Найдено: 2");
    expect(calls[0].url).toBe("/projects/17/drafts/bulk-dismiss");
    expect(calls[0].body.filter).toEqual({ draft_ids: [7, 9] });
    expect(screen.getByRole("button", { name: "Отклонить 2 черновика (можно отменить)" })).toBeInTheDocument();
  });

  it("explains a stale preview instead of closing a different set", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    respond({ dry_run: true, count: 4, sample: [] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    await screen.findByText("Найдено: 4");
    respond({ detail: { code: "bulk_preview_stale", expected: 4, actual: 5 } }, 409);
    fireEvent.click(screen.getByRole("button", { name: "Закрыть 4 задачи (можно отменить)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Очередь изменилась после предпросмотра");
  });

  it("pluralises Russian nouns", () => {
    const forms: [string, string, string] = ["задачу", "задачи", "задач"];
    expect([1, 2, 5, 11, 21, 22, 112].map((n) => pluralRu(n, forms)))
      .toEqual(["задачу", "задачи", "задач", "задач", "задачу", "задачи", "задач"]);
  });
});

describe("TasksModule triage and «Мои активные»", () => {
  const base = (overrides: Partial<ComponentProps<typeof TasksModule>> = {}): ComponentProps<typeof TasksModule> => ({
    tasks: [task], filter: "all", members, documents, history,
    completionTaskId: 0, completionNote: "", completionDocumentId: 0, historyTaskId: 0,
    onFilterChange: vi.fn(), onAssign: vi.fn(), onChangeDueDate: vi.fn(), onApproveExternal: vi.fn(), onUpdate: vi.fn(),
    onStartCompletion: vi.fn(), onCancelCompletion: vi.fn(), onCompletionNoteChange: vi.fn(),
    onCompletionDocumentChange: vi.fn(), onLoadHistory: vi.fn(), ...overrides,
  });

  it("shows checkboxes only in manual mode and sends the ticked ids", async () => {
    const tasks = [{ ...task, id: 1, title: "Первая", status: "assigned" }, { ...task, id: 2, title: "Вторая", status: "assigned" },
      { ...task, id: 3, title: "Готовая", status: "completed" }];
    render(<TasksModule {...base({ tasks, projectId: 17, canTriage: true })} />);
    expect(screen.queryByLabelText("Выбрать задачу Первая")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Разобрать очередь" }));
    fireEvent.click(screen.getByLabelText("Выбранные вручную галочками (0)"));
    expect(screen.getByRole("button", { name: "Показать, что попадёт" })).toBeDisabled();
    expect(screen.queryByLabelText("Выбрать задачу Готовая")).toBeNull();
    fireEvent.click(screen.getByLabelText("Выбрать задачу Вторая"));
    respond({ dry_run: true, count: 1, sample: [{ id: 2, title: "Вторая", status: "assigned" }] });
    fireEvent.click(screen.getByRole("button", { name: "Показать, что попадёт" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body.filter).toEqual({ task_ids: [2] });
  });

  it("hides the panel from users without the manager role", () => {
    render(<TasksModule {...base({ projectId: 17, canTriage: false })} />);
    expect(screen.queryByRole("button", { name: "Разобрать очередь" })).toBeNull();
  });

  it("«Мои активные» keeps my or unassigned open tasks due within 14 days or overdue", () => {
    const now = new Date(2026, 9, 14);
    const mine = { ...task, status: "assigned", assignee_user_id: 5 };
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-01" }, 5, now)).toBe(true);
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-28" }, 5, now)).toBe(true);
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-29" }, 5, now)).toBe(false);
    expect(isMyActiveTask({ ...mine, due_date: undefined }, 5, now)).toBe(false);
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-20", assignee_user_id: 6 }, 5, now)).toBe(false);
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-20", assignee_user_id: 0 }, 5, now)).toBe(true);
    expect(isMyActiveTask({ ...mine, due_date: "2026-10-20", status: "cancelled" }, 5, now)).toBe(false);
  });

  it("defaults to «Мои активные» and remembers the chosen filter", () => {
    expect(storedTaskFilter()).toBe("mine");
    rememberTaskFilter("review");
    expect(window.localStorage.getItem(TASK_FILTER_STORAGE_KEY)).toBe("review");
    expect(storedTaskFilter()).toBe("review");
    window.localStorage.setItem(TASK_FILTER_STORAGE_KEY, "bogus");
    expect(storedTaskFilter()).toBe("mine");
  });
});
