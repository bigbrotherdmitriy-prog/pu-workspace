import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContractBulkImportWizard, type BulkContractCandidate } from "./ContractBulkImportWizard";

const candidate: BulkContractCandidate = {
  document_id: 11, document_name: "Договор Д-11.pdf", number: "Д-11", title: "Монтаж",
  contract_kind: "customer", confidence: 0.91, evidence: ["собственный заголовок договора найден"],
  already_linked: false,
};
const other: BulkContractCandidate = {
  document_id: 12, document_name: "Письмо по договору.pdf", number: "Письмо по договору",
  title: "Письмо по договору", contract_kind: "customer", confidence: 0.31,
  evidence: ["исключён: найдена только ссылка на другой договор"],
  reason: "исключён: найдена только ссылка на другой договор", already_linked: false,
};

afterEach(() => cleanup());

function renderWizard(onImport = vi.fn().mockResolvedValue(1)) {
  const onFindCandidates = vi.fn(async (onProgress) => {
    onProgress({ completed: 2, total: 2, jobsCompleted: 1, jobsTotal: 1 });
    return { candidates: [candidate], remaining: [other] };
  });
  render(<ContractBulkImportWizard
    documents={[{ id: 11, name: candidate.document_name, source: "google_drive" }, { id: 12, name: other.document_name, source: "google_drive" }]}
    contracts={[]}
    onFindCandidates={onFindCandidates}
    onImport={onImport}
  />);
  return { onFindCandidates, onImport };
}

describe("ContractBulkImportWizard", () => {
  it("separates candidates from remaining documents and explains the decision", async () => {
    renderWizard();
    fireEvent.click(screen.getByRole("button", { name: "Найти договоры" }));
    expect(await screen.findByText("Найдено кандидатов: 1")).toBeInTheDocument();
    expect(screen.getByText("Остальные документы: 1")).toBeInTheDocument();
    expect(screen.getByText("исключён: найдена только ссылка на другой договор")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Договор Д-11/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Письмо по договору/ })).not.toBeChecked();
  });

  it("does not create contracts until the explicit final confirmation", async () => {
    const { onImport } = renderWizard();
    fireEvent.click(screen.getByRole("button", { name: "Найти договоры" }));
    await screen.findByText("Найдено кандидатов: 1");
    fireEvent.click(screen.getByRole("button", { name: "Проверить 1 предложений" }));
    expect(onImport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Создать и привязать всё дерево" }));
    await waitFor(() => expect(onImport).toHaveBeenCalledOnce());
  });

  it("allows a user to add an excluded document manually", async () => {
    renderWizard();
    fireEvent.click(screen.getByRole("button", { name: "Найти договоры" }));
    await screen.findByText("Найдено кандидатов: 1");
    fireEvent.click(screen.getByRole("checkbox", { name: /Письмо по договору/ }));
    expect(screen.getByRole("button", { name: "Проверить 2 предложений" })).toBeEnabled();
  });
});
