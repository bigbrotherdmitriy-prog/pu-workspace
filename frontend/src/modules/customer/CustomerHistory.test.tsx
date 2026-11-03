import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { CustomerPortal } from "./CustomerPortal";
vi.mock("../../api/client", async (original) => ({ ...(await original<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); mockApi.mockReset(); });
const projects = [{ id: 4, name: "Первый тестовый проект" }, { id: 5, name: "Второй тестовый проект" }];
const summary = (id = 4) => ({ project: projects[id === 4 ? 0 : 1], estimates: [], acts: [{ id: id === 4 ? 8 : 9, number: "1", period_from: "2026-10-01", period_to: "2026-10-28", status: "sent", total_ex_vat: "4000.00", customer_status: "pending", customer_comment: null }] });
const event = { id: 1, created_at: "2026-10-30T12:00:00+00:00", user_id: 2, decision: "approved", comment: "Синтетическая история решения" };
function prepare(history: () => Promise<unknown>) {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/summary")) return summary(path.includes("/5/") ? 5 : 4);
    if (path.endsWith("/progress")) return { entries: [] };
    if (path.endsWith("/decision-history")) return history();
    throw new Error("Unexpected synthetic request");
  });
}
it("loads a bounded decision history only after explicit click", async () => {
  prepare(async () => ({ events: [event], has_more: true }));
  render(<CustomerPortal projects={projects} />);
  await screen.findByText("Акт № 1");
  expect(mockApi.mock.calls.some(([path]) => String(path).endsWith("/decision-history"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "История решений" }));
  expect(await screen.findByText(event.comment)).toBeTruthy();
  expect(screen.getByText("Согласовано")).toBeTruthy();
  expect(screen.getByText("Показаны последние события.")).toBeTruthy();
  expect(mockApi).toHaveBeenCalledWith("/customer/projects/4/ks-acts/8/decision-history");
});
it("shows an error and lets the customer retry history loading", async () => {
  let attempts = 0;
  prepare(async () => { if (++attempts === 1) throw new Error("Синтетический отказ истории"); return { events: [], has_more: false }; });
  render(<CustomerPortal projects={projects} />);
  await screen.findByText("Акт № 1");
  fireEvent.click(screen.getByRole("button", { name: "История решений" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Синтетический отказ истории");
  fireEvent.click(screen.getByRole("button", { name: "История решений" }));
  expect(await screen.findByText("История решений пока пуста.")).toBeTruthy();
});
it("does not display a late history response after switching projects", async () => {
  let resolveHistory!: (value: unknown) => void;
  prepare(() => new Promise((resolve) => { resolveHistory = resolve; }));
  render(<CustomerPortal projects={projects} />);
  await screen.findByText("Акт № 1");
  fireEvent.click(screen.getByRole("button", { name: "История решений" }));
  await waitFor(() => expect(resolveHistory).toBeTypeOf("function"));
  fireEvent.change(screen.getByRole("combobox", { name: "Проект" }), { target: { value: "5" } });
  await screen.findByRole("heading", { name: "Второй тестовый проект" });
  await act(async () => resolveHistory({ events: [event], has_more: false }));
  expect(screen.queryByText(event.comment)).toBeNull();
});
it("keeps history available without offering another decision on an approved act", async () => {
  const approved = { ...summary(), acts: summary().acts.map((a) => ({ ...a, customer_status: "approved" })) };
  mockApi.mockImplementation(async (path: string) => path.endsWith("/summary") ? approved : { entries: [] });
  render(<CustomerPortal projects={projects} />);
  await screen.findByText("Акт № 1");
  expect(screen.queryByRole("button", { name: "Согласовать" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Отклонить" })).toBeNull();
  expect(screen.getByRole("button", { name: "История решений" })).toBeTruthy();
});
it("ignores a late summary response from the previous project", async () => {
  let resolveFirst!: (value: unknown) => void;
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/customer/projects/4/summary") return new Promise((resolve) => { resolveFirst = resolve; });
    if (path.endsWith("/summary")) return summary(5);
    return { entries: [] };
  });
  render(<CustomerPortal projects={projects} />);
  await waitFor(() => expect(resolveFirst).toBeTypeOf("function"));
  fireEvent.change(screen.getByRole("combobox", { name: "Проект" }), { target: { value: "5" } });
  await screen.findByRole("heading", { name: "Второй тестовый проект" });
  await act(async () => resolveFirst(summary(4)));
  expect(screen.getByRole("heading", { name: "Второй тестовый проект" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Первый тестовый проект" })).toBeNull();
});
