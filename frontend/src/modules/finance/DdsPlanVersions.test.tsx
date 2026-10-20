import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { DdsPlanVersions } from "./DdsPlanVersions";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const versions = [
  { id: 1, version_number: 1, label: "Исходный план", effective_from: null, created_at: "2026-10-01T10:00:00Z", totals: { inflow: "1000.00", outflow: "600.00" } },
];

it("renders the saved plan versions list", async () => {
  mockApi.mockResolvedValue(versions);
  render(<DdsPlanVersions projectId={7} contractId={null} currency="RUB" />);
  expect(await screen.findByText("Исходный план")).toBeTruthy();
});

it("saves a new plan version with the entered label", async () => {
  mockApi.mockImplementation(async (path: string, opts?: { method?: string }) => {
    if (opts?.method === "POST") return { id: 2 };
    return versions;
  });
  render(<DdsPlanVersions projectId={7} contractId={null} currency="RUB" />);
  await screen.findByText("Исходный план");
  fireEvent.change(screen.getByLabelText("Название версии"), { target: { value: "После переноса оплат" } });
  fireEvent.click(screen.getByText("Сохранить версию"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/7/dds-plan-versions",
    expect.objectContaining({ method: "POST", body: expect.stringContaining("После переноса оплат") })));
});

it("compares a version with the current plan and hides unchanged articles", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.includes("/compare")) return {
      target_label: "Текущий план",
      articles: [
        { title: "Материалы", direction: "outflow", base_total: "100.00", target_total: "150.00", difference: "50.00", months: [] },
        { title: "АР", direction: "outflow", base_total: "200.00", target_total: "200.00", difference: "0.00", months: [] },
      ],
      totals: { inflow: { base: "0.00", target: "0.00", difference: "0.00" }, outflow: { base: "300.00", target: "350.00", difference: "50.00" } },
      added: [], removed: [],
    };
    return versions;
  });
  render(<DdsPlanVersions projectId={7} contractId={null} currency="RUB" />);
  await screen.findByText("Исходный план");
  fireEvent.click(screen.getByLabelText("Сравнить версию Исходный план с текущим планом"));
  expect(await screen.findByText("Сравнение: «Исходный план» → текущий план")).toBeTruthy();
  expect(screen.getByText("Материалы")).toBeTruthy();
  expect(screen.queryByText("АР")).toBeNull();
});
