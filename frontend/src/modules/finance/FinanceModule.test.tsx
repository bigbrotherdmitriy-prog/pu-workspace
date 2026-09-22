import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FinanceModule } from "./FinanceModule";

afterEach(cleanup);

describe("finance document ingress", () => {
  it("offers a direct invoice or act upload entrypoint", () => {
    const onUpload = vi.fn();
    render(<FinanceModule
      finance={null}
      candidates={[]}
      contracts={[]}
      selectedContractId={0}
      onSelectContract={vi.fn()}
      onPrepare={vi.fn()}
      onUseCandidate={vi.fn()}
      onUpload={onUpload}
      onUploadFinance={vi.fn()}
      onOpenSchedule={vi.fn()}
      onReload={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Загрузить счёт или акт" }));
    expect(onUpload).toHaveBeenCalledOnce();
  });

  it("makes budget and planned cash-flow uploads explicit after selecting a contract", () => {
    const onUploadFinance = vi.fn();
    render(<FinanceModule
      finance={null}
      candidates={[]}
      contracts={[{ id: 7, number: "Д-7", title: "Монтаж" }]}
      selectedContractId={7}
      onSelectContract={vi.fn()}
      onPrepare={vi.fn()}
      onUseCandidate={vi.fn()}
      onUpload={vi.fn()}
      onUploadFinance={onUploadFinance}
      onOpenSchedule={vi.fn()}
      onReload={vi.fn()}
    />);

    const budget = new File(["budget"], "budget.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    const cashFlow = new File(["cash-flow"], "dds.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Файл бюджета"), { target: { files: [budget] } });
    fireEvent.change(screen.getByLabelText("Файл планового ДДС"), { target: { files: [cashFlow] } });

    expect(onUploadFinance).toHaveBeenNthCalledWith(1, [budget], 7, "budget");
    expect(onUploadFinance).toHaveBeenNthCalledWith(2, [cashFlow], 7, "cash-flow");
  });

  it("opens the schedule in its separate workspace", () => {
    const onOpenSchedule = vi.fn();
    render(<FinanceModule
      finance={null}
      candidates={[]}
      contracts={[{ id: 7, number: "Д-7", title: "Монтаж" }]}
      selectedContractId={7}
      onSelectContract={vi.fn()}
      onPrepare={vi.fn()}
      onUseCandidate={vi.fn()}
      onUpload={vi.fn()}
      onUploadFinance={vi.fn()}
      onOpenSchedule={onOpenSchedule}
      onReload={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Открыть ГПР" }));
    expect(onOpenSchedule).toHaveBeenCalledOnce();
  });
});
