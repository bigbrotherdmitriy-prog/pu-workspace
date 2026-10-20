import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { InvoiceQuickConfirm } from "./InvoiceQuickConfirm";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function setup(status = "proposed") {
  const proposal = {
    id: 55,
    counterparty: "ООО «Сириус»",
    amount: "12345.00",
    currency: "RUB",
    payment_purpose: "Оплата по счёту",
    status,
    planned_date: null,
  };
  const suggestions = {
    suggestions: [
      { plan_entry_id: 10, title: "АР", remaining: "5000.00", reasons: ["Совпадение контрагента"] },
      { plan_entry_id: 11, title: "Материалы", remaining: "20000.00", reasons: ["Похожая сумма"] },
    ],
    recommended: 10,
    additional_recommended: false,
    ai_reason: "Нет подходящей строки",
  };
  mockApi.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/projects/7/invoice-extraction-proposals/55/plan-line-suggestions") return suggestions as never;
    if (path === "/execution/invoice-extraction-proposals/55/confirm") {
      expect(init?.method).toBe("POST");
      return { ok: true } as never;
    }
    if (path === "/execution/invoice-extraction-proposals/55") return proposal as never;
    throw new Error(`unexpected ${path}`);
  });
}

it("shows counterparty and amount, preselects recommended plan line", async () => {
  setup();
  render(<InvoiceQuickConfirm projectId={7} proposalId={55} onClose={() => {}} />);
  await screen.findByText("ООО «Сириус»");
  expect(screen.getByText(/12\s?345/)).toBeTruthy();
  const select = screen.getByLabelText("Строка плана для счёта") as HTMLSelectElement;
  expect(select.value).toBe("10");
});

it("confirms with plan_entry_id and shows success", async () => {
  setup();
  render(<InvoiceQuickConfirm projectId={7} proposalId={55} onClose={() => {}} />);
  await screen.findByText("ООО «Сириус»");
  fireEvent.click(screen.getByText("Подтвердить"));
  await vi.waitFor(() =>
    expect(mockApi).toHaveBeenCalledWith(
      "/execution/invoice-extraction-proposals/55/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ plan_entry_id: 10 }) }),
    ),
  );
  await screen.findByText("Счёт подтверждён");
});

it("sends additional:true when additional option is chosen", async () => {
  setup();
  render(<InvoiceQuickConfirm projectId={7} proposalId={55} onClose={() => {}} />);
  await screen.findByText("ООО «Сириус»");
  fireEvent.change(screen.getByLabelText("Строка плана для счёта"), { target: { value: "additional" } });
  fireEvent.click(screen.getByText("Подтвердить"));
  await vi.waitFor(() =>
    expect(mockApi).toHaveBeenCalledWith(
      "/execution/invoice-extraction-proposals/55/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ additional: true }) }),
    ),
  );
});

it("shows already-processed message when status is not proposed", async () => {
  setup("confirmed");
  render(<InvoiceQuickConfirm projectId={7} proposalId={55} onClose={() => {}} />);
  expect(await screen.findByText("Этот счёт уже обработан")).toBeTruthy();
  expect(screen.getByText("Закрыть")).toBeTruthy();
});
