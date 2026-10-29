import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { EstimatesModule } from "./EstimatesModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); localStorage.clear(); });

const estimates = { estimates: [{ id: 3, name: "Смета Дубна", source_name: "s.xlsx", total: "10000.00", vat_rate: "22.00", lines_count: 2 }] };
const detail = {
  ...estimates.estimates[0], done_amount: "3000.00",
  lines: [
    { id: 11, section: "Раздел 1", position: "1", code: "ФЕР1", name: "Прокладка кабеля", unit: "м", qty: "100.000", unit_price: "50.00", total: "5000.00", fact_qty: "60.000", billed_qty: "0.000", done_percent: 60 },
    { id: 12, section: "Раздел 1", position: "2", code: "", name: "Монтаж щита", unit: "шт", qty: "2.000", unit_price: "2000.00", total: "4000.00", fact_qty: "0.000", billed_qty: "0.000", done_percent: 0 },
  ],
};
const acts = { acts: [{ id: 5, number: "1", period_from: "2026-10-01", period_to: "2026-10-28", status: "draft", total_ex_vat: "3000.00", lines_count: 1 }] };

function mockAll() {
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (path.endsWith("/ks-acts") && init?.method === "POST") return { id: 6 };
    if (path.includes("/ks-preview")) return { lines: [{ position: "1", name: "Прокладка кабеля", unit: "м", qty: "60.000", unit_price: "50.00", amount: "3000.00" }], total_ex_vat: "3000.00", warnings: [] };
    if (path.endsWith("/ks-acts")) return acts;
    if (path.endsWith("/estimates/3")) return detail;
    if (path.endsWith("/estimates")) return estimates;
    return {};
  });
}

it("shows the estimate with fact and percent and the acts", async () => {
  mockAll();
  render(<EstimatesModule projectId={7} />);
  expect(await screen.findByText("Прокладка кабеля")).toBeTruthy();
  expect(screen.getByText("Раздел 1")).toBeTruthy();
  expect(screen.getAllByText("60").length).toBeGreaterThan(0);
  expect(await screen.findByText(/Акт № 1/)).toBeTruthy();
});

it("edits a price on blur", async () => {
  mockAll();
  render(<EstimatesModule projectId={7} />);
  const input = await screen.findByLabelText("Цена позиции 1");
  fireEvent.change(input, { target: { value: "55" } });
  fireEvent.blur(input);
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => String(c[0]).includes("/estimate-lines/11"));
    expect(call).toBeTruthy();
    expect(String((call![1] as { body: string }).body)).toContain("55");
  });
});

it("previews and creates a KS act for the period", async () => {
  mockAll();
  render(<EstimatesModule projectId={7} />);
  fireEvent.click(await screen.findByText("Что войдёт в акт"));
  expect(await screen.findByText(/Итого без НДС/)).toBeTruthy();
  fireEvent.click(screen.getByText("Сформировать акт"));
  await waitFor(() => expect(mockApi.mock.calls.some((c) => String(c[0]).endsWith("/ks-acts") && (c[1] as { method?: string } | undefined)?.method === "POST")).toBe(true));
});

it("uploads the file for preview with the CSRF token and shows the parsed rows", async () => {
  document.cookie = "pu_csrf=tok-5";
  mockApi.mockImplementation(async () => ({ estimates: [] }));
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ lines: [{ position: "1", name: "Разработка грунта", unit: "м3", qty: "10", unit_price: "100", total: "1000", section: "" }], lines_total: 1, warnings: [], detected: { columns: { name: 2 } } }) });
  vi.stubGlobal("fetch", fetchMock);
  render(<EstimatesModule projectId={7} />);
  const input = await screen.findByLabelText("Файл сметы");
  fireEvent.change(input, { target: { files: [new File(["x"], "смета.xlsx")] } });
  fireEvent.click(screen.getByText("Проверить файл"));
  expect(await screen.findByText("Разработка грунта")).toBeTruthy();
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/projects/7/estimates/import?");
  expect(String(url)).toContain("preview=true");
  expect((init as { headers: Record<string, string> }).headers["X-CSRF-Token"]).toBe("tok-5");
  document.cookie = "pu_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

it("shows the empty hint", async () => {
  mockApi.mockResolvedValue({ estimates: [] });
  render(<EstimatesModule projectId={7} />);
  expect(await screen.findByText(/Смет пока нет/)).toBeTruthy();
});
