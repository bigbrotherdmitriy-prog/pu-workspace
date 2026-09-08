import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { ScheduleGraphEditor } from "./ScheduleGraphEditor";
afterEach(cleanup);

const fixture = (overrides: Record<string, unknown> = {}) => ({
  baseline_id: 8, version: 2, status: "draft", graph_revision: 3, planning_mode: "calendar_graph", project_start: "2026-09-01",
  items: [{ id: 12, title: "Подготовка", duration_days: 2, is_milestone: false, predecessor_ids: null,
    constraint_type: "asap", constraint_date: null, not_before_date: null, planned_start: "2026-09-01", planned_finish: "2026-09-02" }], plan: null, ...overrides,
});
const deferred = () => { let resolve!: (v: unknown) => void; const promise = new Promise<unknown>(r => { resolve = r; }); return { promise, resolve }; };
const save = () => screen.getByRole("button", { name: "Сохранить и рассчитать" });
const reload = () => screen.getByRole("button", { name: "Обновить серверную версию" });
const duration = () => screen.getByRole("spinbutton", { name: "Длительность #12" });
async function load(api = vi.fn().mockResolvedValue(fixture()), extra = {}) {
  const result = render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit {...extra} />);
  await screen.findByRole("spinbutton", { name: "Длительность #12" }); return { api, ...result };
}
describe("ScheduleGraphEditor real route contract with synthetic API", () => {
  it("shows load state and exact GET route", async () => {
    const pending = deferred(); const api = vi.fn().mockReturnValue(pending.promise);
    render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit />);
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка");
    expect(api.mock.calls[0][0]).toBe("/execution/baselines/8/graph");
    await act(async () => pending.resolve(fixture())); expect(duration()).toHaveValue(2);
  });
  it("keeps server dates while editing; sends complete intent and adopts saved calculation", async () => {
    const result = fixture(); result.items[0] = { ...result.items[0], duration_days: 4, planned_finish: "2026-09-04" };
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockResolvedValueOnce({ ...result, graph_revision: 4 }); const onSaved = vi.fn();
    await load(api, { onSaved }); fireEvent.change(duration(), { target: { value: "4" } });
    expect(screen.getByText("2026-09-01 → 2026-09-02")).toBeInTheDocument(); fireEvent.click(save());
    await screen.findByText("2026-09-01 → 2026-09-04");
    expect(api.mock.calls[1][0]).toBe("/execution/baselines/8/graph");
    expect(api.mock.calls[1][1].method).toBe("PUT");
    const payload = JSON.parse(api.mock.calls[1][1].body); expect(payload.expected_graph_revision).toBe(3); expect(payload.items[0].duration_days).toBe(4);
    expect(payload.items[0]).not.toHaveProperty("planned_finish"); expect(onSaved).toHaveBeenCalledWith(8, 4);
  });
  it("409 preserves edits; reload compares without automatic rebase or POST replay", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new ApiError("secret raw detail", 409, "test"))
      .mockResolvedValueOnce(fixture({ graph_revision: 5 })).mockResolvedValueOnce(fixture({ graph_revision: 6 }));
    await load(api); fireEvent.change(duration(), { target: { value: "7" } }); fireEvent.click(save());
    await screen.findByText(/Версия ГПР изменилась/); expect(duration()).toHaveValue(7); expect(save()).toBeDisabled();
    expect(screen.queryByText(/secret raw/)).not.toBeInTheDocument(); fireEvent.click(reload());
    await screen.findByText(/Сервер: версия 2, ревизия 5/); expect(duration()).toHaveValue(7); expect(save()).toBeDisabled(); expect(api).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("button", { name: "Оставить мои правки поверх новой ревизии" }));
    expect(duration()).toHaveValue(7); expect(api).toHaveBeenCalledTimes(3); fireEvent.click(save());
    await waitFor(() => expect(api).toHaveBeenCalledTimes(4)); expect(JSON.parse(api.mock.calls[3][1].body).expected_graph_revision).toBe(5);
  });
  it("manual reload does not erase dirty edits; explicit discard does", async () => {
    const api = vi.fn().mockResolvedValue(fixture()); await load(api);
    fireEvent.change(duration(), { target: { value: "9" } }); fireEvent.click(reload());
    await screen.findByText(/Сервер: версия/); expect(duration()).toHaveValue(9);
    fireEvent.click(screen.getByRole("button", { name: "Отбросить мои правки и принять серверную версию" })); expect(duration()).toHaveValue(2);
  });
  it.each(["approved", "superseded"])("%s and viewer are read only", async status => {
    await load(vi.fn().mockResolvedValue(fixture({ status }))); expect(save()).toBeDisabled(); expect(duration()).toBeDisabled();
  });
  it("viewer cannot write even if draft", async () => { const { api } = await load(undefined, { canEdit: false }); expect(save()).toBeDisabled(); fireEvent.click(save()); expect(api).toHaveBeenCalledTimes(1); });
  it("does not rebase across membership of graph items", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockResolvedValueOnce(fixture({ graph_revision: 4, items: [] })); await load(api);
    fireEvent.click(reload()); await screen.findByText(/Состав этапов изменился/); expect(screen.getByRole("button", { name: "Оставить мои правки поверх новой ревизии" })).toBeDisabled();
  });
  it("unknown PUT outcome requires reload and no blind retry", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new Error("private trace")); await load(api); fireEvent.click(save());
    await screen.findByText(/Результат сохранения не подтверждён/); expect(save()).toBeDisabled(); expect(api).toHaveBeenCalledTimes(2); expect(screen.queryByText(/private trace/)).not.toBeInTheDocument();
  });
  it("422 preserves editable draft but never exposes raw details", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new ApiError("secret source", 422, "x")); await load(api); fireEvent.click(save());
    await screen.findByText(/Граф отклонён/); expect(save()).toBeEnabled(); expect(screen.queryByText(/secret source/)).not.toBeInTheDocument();
  });
  it("permission revocation hides draft and calculated content", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new ApiError("private", 403, "x")); await load(api); fireEvent.click(save());
    await screen.findByText(/Редактирование скрыто/); expect(screen.queryByText("Подготовка")).not.toBeInTheDocument(); expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });
  it("ignores late GET from previous project/baseline", async () => {
    const pending = deferred(); const api = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(fixture({ baseline_id: 9 }));
    const { rerender } = render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit />);
    rerender(<ScheduleGraphEditor projectId={5} baselineId={9} api={api} canEdit />); await screen.findByRole("spinbutton");
    await act(async () => pending.resolve(fixture({ items: [] }))); expect(duration()).toBeInTheDocument(); expect(api.mock.calls[1][0]).toBe("/execution/baselines/9/graph");
  });
  it("guards double submits and suppresses late PUT callback after unmount", async () => {
    const pending = deferred(); const onSaved = vi.fn(); const api = vi.fn().mockResolvedValueOnce(fixture()).mockReturnValueOnce(pending.promise);
    const { unmount } = await load(api, { onSaved }); fireEvent.click(save()); fireEvent.click(save()); expect(api).toHaveBeenCalledTimes(2);
    unmount(); await act(async () => pending.resolve(fixture({ graph_revision: 4 }))); expect(onSaved).not.toHaveBeenCalled();
  });
  it("supports milestone and constraint input without calculating browser dates", async () => {
    await load(); fireEvent.click(screen.getByRole("checkbox", { name: "Веха #12" })); expect(duration()).toHaveValue(0); expect(duration()).toBeDisabled();
    fireEvent.change(screen.getByRole("combobox", { name: "Ограничение #12" }), { target: { value: "mso" } });
    expect(screen.getByLabelText("Дата ограничения #12")).toBeRequired();
  });
  it("shows empty graph and retryable loading failure", async () => {
    const api = vi.fn().mockRejectedValueOnce(new Error("raw")).mockResolvedValueOnce(fixture({ items: [] }));
    render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit />); await screen.findByRole("alert"); fireEvent.click(reload());
    await screen.findByText(/Этапов пока нет/); expect(screen.queryByText("raw")).not.toBeInTheDocument();
  });
  it("manager approves only explicit reviewed clean revision with exact PATCH and authoritative GET", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockResolvedValueOnce({ id: 8, status: "approved" }).mockResolvedValueOnce(fixture({ graph_revision: 4, status: "approved" }));
    await load(api, { canApprove: true }); fireEvent.click(screen.getByRole("button", { name: "Проверить перед утверждением" })); expect(api).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Подтверждаю утверждение этой ревизии" })); await waitFor(() => expect(api).toHaveBeenCalledTimes(3));
    expect(api.mock.calls[1]).toEqual(["/execution/baselines/8/status", { method: "PATCH", body: JSON.stringify({ status: "approved", expected_status: "draft", expected_graph_revision: 3 }) }]);
    await screen.findByText(/Только чтение/); expect(save()).toBeDisabled();
  });
  it("dirty graph and legacy graph cannot approve; editor never sees approval", async () => {
    const { unmount } = await load(undefined, { canApprove: true }); fireEvent.change(duration(), { target: { value: "8" } });
    expect(screen.getByRole("button", { name: "Проверить перед утверждением" })).toBeDisabled(); unmount();
    const second = await load(vi.fn().mockResolvedValue(fixture({ planning_mode: "legacy_dates" })), { canApprove: true });
    expect(screen.getByRole("button", { name: "Проверить перед утверждением" })).toBeDisabled(); second.unmount();
    await load(); expect(screen.queryByRole("button", { name: "Проверить перед утверждением" })).not.toBeInTheDocument();
  });
  it("approval 409 locks further writes until explicit reconciliation", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new ApiError("raw", 409, "x")); await load(api, { canApprove: true });
    fireEvent.click(screen.getByRole("button", { name: "Проверить перед утверждением" })); fireEvent.click(screen.getByRole("button", { name: "Подтверждаю утверждение этой ревизии" }));
    await screen.findByText(/Утверждение не подтверждено/); expect(save()).toBeDisabled(); expect(api).toHaveBeenCalledTimes(2);
  });
  it("a new save invalidates an already opened approval review", async () => {
    const api = vi.fn().mockResolvedValueOnce(fixture()).mockResolvedValueOnce(fixture({ graph_revision: 4 }));
    await load(api, { canApprove: true }); fireEvent.click(screen.getByRole("button", { name: "Проверить перед утверждением" }));
    expect(screen.getByRole("group", { name: "Подтверждение утверждения ГПР" })).toBeInTheDocument();
    fireEvent.click(save()); await screen.findByText(/Граф сохранён/);
    expect(screen.queryByRole("group", { name: "Подтверждение утверждения ГПР" })).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(2);
  });
  it("rejects wrong-baseline responses and invalid selection without calls", async () => {
    const api = vi.fn().mockResolvedValue(fixture({ baseline_id: 999 })); const first = render(<ScheduleGraphEditor projectId={4} baselineId={8} api={api} canEdit />);
    await screen.findByRole("alert"); expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument(); first.unmount();
    render(<ScheduleGraphEditor projectId={0} baselineId={8} api={api} canEdit />); expect(screen.getByText(/Выберите проект/)).toBeInTheDocument(); expect(api).toHaveBeenCalledTimes(1);
  });
});
