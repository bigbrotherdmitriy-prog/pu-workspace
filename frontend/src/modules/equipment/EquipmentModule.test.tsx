import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { EquipmentModule } from "./EquipmentModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const unit = {
  id: 1, unit_no: 1, name: "Насос Grundfos", type_mark: "CR 5", manufacturer: "Grundfos",
  serial_number: null, location: null, status: "planned", warranty_until: null,
  service_interval_months: 12, next_service_date: "2026-01-10", service_overdue: true,
};
const summary = { total: 1, by_status: { planned: 1, received: 0, installed: 0, commissioned: 0 }, with_serial: 0, service_overdue: 1 };

it("shows table, counters and overdue mark", async () => {
  mockApi.mockResolvedValue({ units: [unit], summary });
  render(<EquipmentModule projectId={7} />);
  expect(await screen.findByText("Насос Grundfos")).toBeTruthy();
  expect(screen.getAllByText(/просрочено/).length).toBeGreaterThan(1);
  expect(screen.getByText(/Всего/)).toBeTruthy();
});

it("generate calls POST and shows result", async () => {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (init?.method === "POST") return { created: 3, existing: 1 };
    return { units: [unit], summary };
  });
  render(<EquipmentModule projectId={7} />);
  await screen.findByText("Насос Grundfos");
  fireEvent.click(screen.getByText("Создать из рабочей документации"));
  expect(await screen.findByText("Создано: 3, уже было: 1")).toBeTruthy();
});

it("saves serial number on blur", async () => {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (init?.method === "PATCH") return { ...unit, serial_number: "SN-1" };
    return { units: [unit], summary };
  });
  render(<EquipmentModule projectId={7} />);
  const input = await screen.findByLabelText("Заводской номер Насос Grundfos 1");
  fireEvent.change(input, { target: { value: "SN-1" } });
  fireEvent.blur(input);
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("SN-1");
  });
});

it("shows error on duplicate serial", async () => {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (init?.method === "PATCH") throw new Error("duplicate serial");
    return { units: [unit], summary };
  });
  render(<EquipmentModule projectId={7} />);
  const input = await screen.findByLabelText("Заводской номер Насос Grundfos 1");
  fireEvent.change(input, { target: { value: "SN-1" } });
  fireEvent.blur(input);
  expect((await screen.findByRole("alert")).textContent).toContain("duplicate serial");
});

it("shows empty state", async () => {
  mockApi.mockResolvedValue({ units: [], summary: { ...summary, total: 0, service_overdue: 0 } });
  render(<EquipmentModule projectId={7} />);
  expect(await screen.findByText(/Оборудования пока нет/)).toBeTruthy();
});


it("marks the service as done today", async () => {
  mockApi.mockImplementation(async (_p: string, init?: { method?: string }) => (init?.method === "PATCH" ? { ...unit, service_overdue: false } : { units: [unit], summary }));
  render(<EquipmentModule projectId={7} />);
  fireEvent.click(await screen.findByLabelText("ТО выполнено Насос Grundfos 1"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "PATCH");
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("last_service_date");
  });
});
