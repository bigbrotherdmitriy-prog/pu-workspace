import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ContractStagesRegister } from "./ContractStagesRegister";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows two stages of one object with coefficient and posts act extraction", async () => {
  let registerCalls = 0;
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/projects/1/contracts/2/stage-register") {
      registerCalls += 1;
      return {
        items: [
          { id: 11, object_name: "Объект А", stage_number: 1, title: "Работы 1", planned_start: "2026-01-01", planned_finish: "2026-02-01",
            amount: "1000.00", coefficient: "0.08960", vat: "20", act: null, planned_receipt_date: null, actual_receipt_date: null, overdue: false },
          { id: 12, object_name: "Объект А", stage_number: 2, title: "Работы 2", planned_start: "2026-02-01", planned_finish: "2026-03-01",
            amount: "2000.00", coefficient: "0.08960", vat: "20", act: null, planned_receipt_date: null, actual_receipt_date: null, overdue: false },
        ],
        by_object: { "Объект А": { price: "3000.00", acted: "0.00", received: "0.00", remaining: "3000.00" } },
        totals: {},
        next_act_number: "12",
      };
    }
    if (path === "/projects/1/contracts/2/stages/11/act") return { id: 1 };
    return {};
  });

  render(<ContractStagesRegister projectId={1} contractId={2} currency="RUB" />);
  expect(await screen.findByText("Объект А")).toBeTruthy();
  expect(screen.getAllByText("0.08960")).toHaveLength(2);
  const numberInput = screen.getByLabelText("Номер следующего акта") as HTMLInputElement;
  expect(numberInput.value).toBe("12");
  const btn = screen.getByLabelText("Сформировать акт по этапу 1");
  fireEvent.click(btn);
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/1/contracts/2/stages/11/act",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ number: "12" }) })));
  expect(registerCalls).toBeGreaterThanOrEqual(1);
});

it("sends overridden act number when field is changed", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/projects/1/contracts/2/stage-register") {
      return {
        items: [
          { id: 11, object_name: null, stage_number: 1, title: "Работы 1", planned_start: null, planned_finish: null,
            amount: "1000.00", coefficient: "0.08960", vat: "20", act: null, planned_receipt_date: null, actual_receipt_date: null, overdue: false },
        ],
        by_object: {},
        totals: {},
        next_act_number: "12",
      };
    }
    if (path === "/projects/1/contracts/2/stages/11/act") return { id: 1 };
    return {};
  });

  render(<ContractStagesRegister projectId={1} contractId={2} currency="RUB" />);
  const numberInput = await screen.findByLabelText("Номер следующего акта");
  fireEvent.change(numberInput, { target: { value: "15" } });
  fireEvent.click(screen.getByLabelText("Сформировать акт по этапу 1"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/1/contracts/2/stages/11/act",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ number: "15" }) })));
});

it("shows error alert on 409 response", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/projects/1/contracts/2/stage-register") {
      return {
        items: [
          { id: 11, object_name: null, stage_number: 1, title: "Работы 1", planned_start: null, planned_finish: null,
            amount: "1000.00", coefficient: "0.08960", vat: "20", act: null, planned_receipt_date: null, actual_receipt_date: null, overdue: false },
        ],
        by_object: {},
        totals: {},
        next_act_number: "12",
      };
    }
    if (path === "/projects/1/contracts/2/stages/11/act") {
      throw new Error("Акт с номером 12 уже существует");
    }
    return {};
  });

  render(<ContractStagesRegister projectId={1} contractId={2} currency="RUB" />);
  await screen.findByLabelText("Номер следующего акта");
  fireEvent.click(screen.getByLabelText("Сформировать акт по этапу 1"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Акт с номером 12 уже существует");
});

it("shows draft act and signs it via date input", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path === "/projects/1/contracts/2/stage-register") {
      return {
        items: [
          { id: 11, object_name: null, stage_number: 1, title: "Работы 1", planned_start: null, planned_finish: null,
            amount: "1000.00", coefficient: "0.08960", vat: "20", act: { id: 5, number: "4", status: "draft", act_date: null },
            planned_receipt_date: null, actual_receipt_date: null, overdue: false },
        ],
        by_object: {},
        totals: {},
        next_act_number: "5",
      };
    }
    if (path === "/projects/1/acceptance-acts/5/signed") return { id: 5 };
    return {};
  });

  render(<ContractStagesRegister projectId={1} contractId={2} currency="RUB" />);
  expect(await screen.findByText(/№ 4 · черновик/)).toBeTruthy();
  fireEvent.click(screen.getByText("Подписан"));
  fireEvent.change(screen.getByLabelText("Дата подписания акта"), { target: { value: "2026-10-20" } });
  fireEvent.click(screen.getByText("Сохранить"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/1/acceptance-acts/5/signed",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ act_date: "2026-10-20" }) })));
});

it("shows extract button when items are empty", async () => {
  mockApi.mockImplementation(async () => ({ items: [], by_object: {}, totals: {}, next_act_number: "1" }));
  render(<ContractStagesRegister projectId={1} contractId={2} currency="RUB" />);
  expect(await screen.findByText("Найти этапы в договоре")).toBeTruthy();
});
