import { expect, release, start, test, type StorageApi } from "./storage-fixtures";
import type { Graph } from "../src/modules/schedule/graphReadModel";

const graph = (id = 8): Graph => ({
  baseline_id: id, version: 2, status: "draft", graph_revision: 3,
  planning_mode: "calendar_graph", project_start: "2026-09-01",
  items: [{ id: 12, title: "Synthetic preparation", duration_days: 2, is_milestone: false,
    predecessor_ids: null, constraint_type: "asap", constraint_date: null,
    not_before_date: null, planned_start: "2026-09-01", planned_finish: "2026-09-02" }],
});
function register(mock: StorageApi, projectId: number, current: () => Graph) {
  mock.reply("GET", `/execution/overview?project_id=${projectId}`, () => ({ body: {
    baselines: [{ id: current().baseline_id, name: "Synthetic GPR", version: 2,
      status: current().status, is_current: current().status === "approved" }],
    schedule: [], budget: [], cash_flow: [], procurement: [], acts: [], summary: {
      budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0,
      budget_variance: 0, cash_balance_forecast: 0, cash_gap: 0, delayed_schedule: 0,
      late_procurement: 0, acts_pending: 0, pending_payments: 0, unlinked_invoices: 0,
    },
  } }));
  // This scenario exercises GPR, not forecast acceptance. Explicit synthetic denial.
  mock.reply("GET", `/execution/forecast/${projectId}`, { status: 503, body: { detail: "Synthetic forecast not enabled" } });
  mock.reply("GET", `/execution/baselines/${current().baseline_id}/graph`, () => ({ body: current() }));
}
const writes = (mock: StorageApi) => mock.requests.filter(r => ["PUT", "PATCH", "POST", "DELETE"].includes(r.method));

test("GPR: actual App opens register, saves exact graph revision then explicitly approves it", async ({ page, mock }) => {
  mock.currentUser = { id: 900, name: "Synthetic Operator", is_admin: false };
  mock.membersByProject.set(2, [{ membership_id: 1, user_id: 900, name: "Synthetic Operator", role: "manager" }]);
  let current = graph(); register(mock, 2, () => current);
  mock.reply("PUT", "/execution/baselines/8/graph", request => {
    expect(request.postDataJSON()).toEqual({ expected_graph_revision: 3, project_start: "2026-09-01", items: [{
      id: 12, duration_days: 4, is_milestone: false, predecessor_ids: null, constraint_type: "asap",
      constraint_date: null, not_before_date: null,
    }] });
    expect(request.headers()["x-csrf-token"]).toBe("synthetic-csrf-only");
    current = { ...current, graph_revision: 4, items: [{ ...current.items[0], duration_days: 4, planned_finish: "2026-09-04" }] };
    return { body: current };
  });
  mock.reply("PATCH", "/execution/baselines/8/status", request => {
    expect(request.postDataJSON()).toEqual({ status: "approved", expected_status: "draft", expected_graph_revision: 4 });
    current = { ...current, status: "approved", graph_revision: 5 };
    return { body: { id: 8, status: "approved" } };
  });
  await start(page); await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await page.getByRole("button", { name: "Проверить и утвердить", exact: true }).click();
  const editor = page.getByRole("region", { name: "Редактор графа ГПР", exact: true });
  await expect(editor.getByLabel("Длительность #12")).toHaveValue("2"); expect(writes(mock)).toHaveLength(0);
  await editor.getByLabel("Длительность #12").fill("4");
  await expect(editor.getByRole("button", { name: "Проверить перед утверждением" })).toBeDisabled();
  await editor.getByRole("button", { name: "Сохранить и рассчитать" }).click();
  await expect(editor.getByText("2026-09-01 → 2026-09-04", { exact: true })).toBeVisible();
  await editor.getByRole("button", { name: "Проверить перед утверждением" }).click(); expect(writes(mock)).toHaveLength(1);
  await editor.getByRole("button", { name: "Подтверждаю утверждение этой ревизии" }).click();
  await expect(editor.getByText(/Только чтение/)).toBeVisible();
  await expect(editor.getByRole("button", { name: "Сохранить и рассчитать" })).toBeDisabled();
  expect(writes(mock).map(r => `${r.method} ${r.path}`)).toEqual(["PUT /execution/baselines/8/graph", "PATCH /execution/baselines/8/status"]);
});

test("GPR: actual App preserves 409 draft and requires separate reload, rebase and save", async ({ page, mock }) => {
  mock.currentUser = { id: 900, name: "Synthetic Operator", is_admin: false };
  mock.membersByProject.set(2, [{ membership_id: 1, user_id: 900, name: "Synthetic Operator", role: "manager" }]);
  let current = graph(); let attempts = 0; register(mock, 2, () => current);
  mock.reply("PUT", "/execution/baselines/8/graph", request => {
    attempts += 1;
    if (attempts === 1) { current = { ...current, graph_revision: 6 }; return { status: 409, body: { detail: "schedule_graph_revision_changed" } }; }
    expect(request.postDataJSON()).toMatchObject({ expected_graph_revision: 6, items: [{ id: 12, duration_days: 7 }] });
    current = { ...current, graph_revision: 7, items: [{ ...current.items[0], duration_days: 7, planned_finish: "2026-09-07" }] };
    return { body: current };
  });
  await start(page); await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await page.getByRole("button", { name: "Проверить и утвердить", exact: true }).click();
  const editor = page.getByRole("region", { name: "Редактор графа ГПР", exact: true });
  await editor.getByLabel("Длительность #12").fill("7"); await editor.getByRole("button", { name: "Сохранить и рассчитать" }).click();
  await expect(editor.getByText(/Версия ГПР изменилась/)).toBeVisible();
  await expect(editor.getByLabel("Длительность #12")).toHaveValue("7");
  await expect(editor.getByRole("button", { name: "Сохранить и рассчитать" })).toBeDisabled();
  await editor.getByRole("button", { name: "Обновить серверную версию" }).click();
  await expect(editor.getByText(/Сервер: версия 2, ревизия 6/)).toBeVisible();
  await expect(editor.getByLabel("Длительность #12")).toHaveValue("7"); expect(attempts).toBe(1);
  await editor.getByRole("button", { name: "Оставить мои правки поверх новой ревизии" }).click(); expect(attempts).toBe(1);
  await editor.getByRole("button", { name: "Сохранить и рассчитать" }).click();
  await expect(editor.getByText("2026-09-01 → 2026-09-07", { exact: true })).toBeVisible(); expect(attempts).toBe(2);
  expect(writes(mock).every(r => r.method === "PUT")).toBe(true);
});

test("GPR: slower new-project membership never inherits old manager approval", async ({ page, mock }) => {
  mock.currentUser = { id: 900, name: "Synthetic Operator", is_admin: false };
  mock.membersByProject.set(2, [{ membership_id: 1, user_id: 900, name: "Manager in A", role: "manager" }]);
  register(mock, 2, () => graph(8)); register(mock, 1, () => graph(9));
  await start(page); await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await page.getByRole("button", { name: "Проверить и утвердить", exact: true }).click();
  await expect(page.getByRole("button", { name: "Проверить перед утверждением" })).toBeEnabled();
  const members = mock.hold(url => url.pathname === "/projects/1/members");
  await page.getByRole("combobox").first().selectOption("1"); await members.request;
  await page.getByRole("button", { name: "Проверить и утвердить", exact: true }).click();
  const editor = page.getByRole("region", { name: "Редактор графа ГПР", exact: true });
  await expect(editor.getByLabel("Длительность #12")).toBeDisabled();
  await expect(editor.getByRole("button", { name: "Проверить перед утверждением" })).toHaveCount(0);
  await release(page, members, { members: [{ membership_id: 2, user_id: 900, name: "Viewer in B", role: "viewer" }] });
  await expect(editor.getByRole("button", { name: "Сохранить и рассчитать" })).toBeDisabled();
  await expect(editor.getByRole("button", { name: "Проверить перед утверждением" })).toHaveCount(0);
  expect(writes(mock)).toHaveLength(0);
});
