import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalsModule, type Proposal } from "./ProposalsModule";

afterEach(cleanup);

const baseProposal: Proposal = {
  id: 7,
  folder_name: "Договоры",
  status: "waiting_confirmation",
  copy_folder_id: "safe-copy-7",
  originals_modified: false,
  actions: [{
    id: 11,
    source: "Скан 001.pdf",
    proposed_name: "2026-09-03_Договор_№951.pdf",
    target_folder: "Договоры",
    user_decision: "pending",
    confidence: 0.92,
    reasoning: "Тип и номер найдены в документе.",
  }],
};

function props(proposal: Proposal = baseProposal) {
  return {
    collapsed: false,
    proposals: [proposal],
    busyProposal: 0,
    targetFolders: ["Договоры", "Неразобранное"],
    onOpenDocuments: vi.fn(),
    onApproveSafe: vi.fn(),
    onConfirmSelected: vi.fn(),
    onApply: vi.fn(),
    onStandardize: vi.fn(),
    onRollback: vi.fn(),
    onDecision: vi.fn(),
    onSave: vi.fn(),
    onEdit: vi.fn(),
    onApplySource: vi.fn(),
    onApplySourceBulk: vi.fn(),
  };
}

describe("ProposalsModule rename review", () => {
  it("shows a before/after preview without changing the source", () => {
    render(<ProposalsModule {...props()} />);
    expect(screen.getByRole("region", { name: "Предпросмотр переименования" })).toBeInTheDocument();
    expect(screen.getByText("Скан 001.pdf")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-09-03_Договор_№951.pdf")).toBeInTheDocument();
    expect(screen.queryByText(/Подтвердить выбранные/)).not.toBeInTheDocument();
  });

  it("records a row decision but does not apply files", () => {
    const callbacks = props();
    render(<ProposalsModule {...callbacks} />);
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "approved" } });
    expect(callbacks.onDecision).toHaveBeenCalledWith(baseProposal.actions[0], "approved");
    expect(callbacks.onApply).not.toHaveBeenCalled();
  });

  it("offers explicit package confirmation only after manual selection", () => {
    const selected = { ...baseProposal, actions: [{ ...baseProposal.actions[0], user_decision: "approved" }] };
    const callbacks = props(selected);
    render(<ProposalsModule {...callbacks} />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить выбранные (1)" }));
    expect(callbacks.onConfirmSelected).toHaveBeenCalledWith(selected);
    expect(callbacks.onApply).not.toHaveBeenCalled();
  });
});
