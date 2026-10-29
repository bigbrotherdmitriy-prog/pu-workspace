import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { CustomerGate } from "./CustomerGate";
import { CustomerPortal } from "./CustomerPortal";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); });

const summary = {
  project: { id: 4, name: "ЦОД Дубна" },
  estimates: [{ id: 1, name: "Смета 1", total: "10000.00", done_amount: "4000.00", percent: 40 }],
  acts: [
    { id: 8, number: "1", period_from: "2026-10-01", period_to: "2026-10-28", status: "sent", total_ex_vat: "4000.00", customer_status: "pending", customer_comment: null },
    { id: 9, number: "2", period_from: "2026-09-01", period_to: "2026-09-30", status: "signed", total_ex_vat: "1000.00", customer_status: "approved", customer_comment: "ок" },
  ],
};

function mockPortal() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.endsWith("/summary")) return summary;
    if (path.endsWith("/progress")) return { entries: [{ work_date: "2026-10-05", work_name: "Прокладка кабеля", unit: "м", qty: "40.000", location: "Этаж 2", object_name: null }] };
    if (init?.method === "POST") return { id: 8 };
    return {};
  });
}

it("shows progress, acts and the work entries without internal data", async () => {
  mockPortal();
  render(<CustomerPortal projects={[{ id: 4, name: "ЦОД Дубна" }]} />);
  expect(await screen.findByText("Смета 1")).toBeTruthy();
  expect(screen.getByText(/Выполнено 40%/)).toBeTruthy();
  expect(screen.getByText("Акт № 1")).toBeTruthy();
  expect(screen.getByText("Подписан")).toBeTruthy();
  expect(await screen.findByText("Прокладка кабеля")).toBeTruthy();
  expect(screen.queryByText(/часов/i)).toBeNull();
});

it("approves an act and requires a comment to reject", async () => {
  mockPortal();
  render(<CustomerPortal projects={[{ id: 4, name: "ЦОД Дубна" }]} />);
  fireEvent.click(await screen.findByText("Согласовать"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).endsWith("/decision"));
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("approved");
  });
  fireEvent.change(screen.getByLabelText("Комментарий к акту 1"), { target: { value: "Объём завышен" } });
  fireEvent.click(screen.getByText("Отклонить"));
  await waitFor(() => {
    const calls = mockApi.mock.calls.filter((c) => String(c[0]).endsWith("/decision"));
    expect(String((calls[calls.length - 1][1] as { body: string }).body)).toContain("Объём завышен");
  });
});

it("gate: opens the customer cabinet only for users without own projects", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/customer/projects") return { projects: [{ id: 4, name: "ЦОД Дубна" }] };
    if (path === "/projects/") return { projects: [] };
    if (path.endsWith("/summary")) return summary;
    return { entries: [] };
  });
  render(<CustomerGate><div>обычное приложение</div></CustomerGate>);
  expect(await screen.findByText("Кабинет заказчика")).toBeTruthy();
  expect(screen.queryByText("обычное приложение")).toBeNull();
});

it("gate: a contractor with own projects, a plain user and a logged-out visitor see the normal app", async () => {
  mockApi.mockImplementation(async (path: string) => (path === "/customer/projects" ? { projects: [{ id: 4, name: "X" }] } : { projects: [{ id: 1, name: "Мой" }] }));
  const first = render(<CustomerGate><div>обычное приложение</div></CustomerGate>);
  expect(await screen.findByText("обычное приложение")).toBeTruthy();
  first.unmount();
  mockApi.mockReset();
  mockApi.mockResolvedValue({ projects: [] });
  const second = render(<CustomerGate><div>обычное приложение</div></CustomerGate>);
  expect(await screen.findByText("обычное приложение")).toBeTruthy();
  second.unmount();
  mockApi.mockReset();
  mockApi.mockRejectedValue(new Error("401"));
  render(<CustomerGate><div>обычное приложение</div></CustomerGate>);
  expect(await screen.findByText("обычное приложение")).toBeTruthy();
});
