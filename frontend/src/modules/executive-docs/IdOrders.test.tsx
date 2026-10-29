import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { IdOrders } from "./IdOrders";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); });

const catalog = [
  { kind: "labor_safety", title: "О назначении ответственного за охрану труда", certificate: "Удостоверение о проверке знаний требований охраны труда" },
  { kind: "hot_work", title: "О назначении ответственного за безопасное проведение огневых работ", certificate: "Удостоверение по огневым работам" },
];
const incomplete = { id: 5, kind: "hot_work", title: catalog[1].title, certificate_required: catalog[1].certificate, number: "12", order_date: "2026-10-20",
  person_name: "Иванов И.И.", position: "прораба", org_name: null, object_name: null, city: null, signer_name: null, signer_position: null, extra: null,
  certificates: [], complete: false, missing: ["копия удостоверения"] };

it("shows what is missing and offers the certificate upload and downloads", async () => {
  mockApi.mockResolvedValue({ catalog, orders: [incomplete] });
  render(<IdOrders projectId={4} />);
  expect(await screen.findByText(/Не хватает: копия удостоверения/)).toBeTruthy();
  expect(screen.getByText("Скачать приказ (Word)")).toBeTruthy();
  expect(screen.getByText("Загрузить копию удостоверения")).toBeTruthy();
  expect(screen.getByText(/без копии удостоверения или ФИО: 1/)).toBeTruthy();
});

it("creates an order with the shared requisites", async () => {
  mockApi.mockImplementation(async (_p: string, init?: { method?: string }) => (init?.method === "POST" ? { id: 6 } : { catalog, orders: [] }));
  render(<IdOrders projectId={4} />);
  await screen.findByText("Приказов пока нет.");
  fireEvent.change(screen.getByLabelText("Организация"), { target: { value: "ООО Монтаж" } });
  fireEvent.change(screen.getByLabelText("ФИО ответственного"), { target: { value: "Петров П.П." } });
  fireEvent.click(screen.getByText("Добавить приказ"));
  await waitFor(() => {
    const call = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "POST");
    expect(call).toBeTruthy();
    const body = JSON.parse(String((call![1] as { body: string }).body));
    expect(body.person_name).toBe("Петров П.П.");
    expect(body.org_name).toBe("ООО Монтаж");
    expect(body.kind).toBe("labor_safety");
  });
  expect(await screen.findByText(/загрузите копию удостоверения/)).toBeTruthy();
  expect(localStorage.getItem("pu-id-orders-defaults:4")).toContain("ООО Монтаж");
});

it("uploads a certificate copy as a raw body", async () => {
  mockApi.mockResolvedValue({ catalog, orders: [incomplete] });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  render(<IdOrders projectId={4} />);
  const input = await screen.findByLabelText("Копия удостоверения для приказа 5");
  fireEvent.change(input, { target: { files: [new File(["x"], "уд.pdf", { type: "application/pdf" })] } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("/projects/4/id-orders/5/certificate?name=");
  expect((fetchMock.mock.calls[0][1] as { headers: Record<string, string> }).headers["Content-Type"]).toBe("application/pdf");
});

it("explains why the package is not ready", async () => {
  mockApi.mockResolvedValue({ catalog, orders: [incomplete] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "Комплект не готов. «О назначении…»: нет — копия удостоверения" }) }));
  render(<IdOrders projectId={4} />);
  fireEvent.click(await screen.findByText("Скачать комплект (zip)"));
  expect((await screen.findByRole("alert")).textContent).toContain("Комплект не готов");
});
