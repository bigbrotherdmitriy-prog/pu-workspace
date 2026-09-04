import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GovernanceModule, type DecisionRow, type RiskRow } from "./GovernanceModule";

afterEach(cleanup);

const risk: RiskRow = {
  id: 1,
  title: "Срок поставки требует подтверждения",
  kind: "schedule",
  criticality: "high",
  status: "needs_confirmation",
  source_name: "Договор.pdf",
  confidence: 0.82,
};

const decision: DecisionRow = {
  id: 2,
  question: "Утвердить новый срок проверки?",
  status: "needs_confirmation",
  source_name: "Протокол.docx",
  confidence: 0.8,
};

describe("GovernanceModule", () => {
  it("shows a compact overview and human-readable statuses", () => {
    render(<GovernanceModule risks={[risk]} decisions={[decision]} onUpdateRisk={vi.fn()} onUpdateDecision={vi.fn()} />);
    expect(screen.getByText("Открытые риски")).toBeInTheDocument();
    expect(screen.getByText("Высокий приоритет")).toBeInTheDocument();
    expect(screen.getAllByText("Нужно подтвердить")).toHaveLength(2);
    expect(screen.queryByText("needs_confirmation")).toBeNull();
  });

  it("keeps risk and decision actions wired", () => {
    const onUpdateRisk = vi.fn();
    const onUpdateDecision = vi.fn();
    render(<GovernanceModule risks={[risk]} decisions={[decision]} onUpdateRisk={onUpdateRisk} onUpdateDecision={onUpdateDecision} />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "Принять" }));
    fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
    expect(onUpdateRisk).toHaveBeenNthCalledWith(1, risk, "confirmed");
    expect(onUpdateRisk).toHaveBeenNthCalledWith(2, risk, "resolved");
    expect(onUpdateDecision).toHaveBeenNthCalledWith(1, decision, "decided");
    expect(onUpdateDecision).toHaveBeenNthCalledWith(2, decision, "dismissed");
  });
});
