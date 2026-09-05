import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AttentionPanel } from "./AttentionPanel";
import { DeadlineDigestPanel } from "./DeadlineDigestPanel";
import { MeetingProposalPanel } from "./MeetingProposalPanel";
import { ObligationDetailPanel } from "./ObligationDetailPanel";
import { RiskDecisionPanel } from "./RiskDecisionPanel";
import type { AttentionItem, Obligation } from "./managementReadModel";

const evidence = { ref: { id: { value: "ev-17" } } };
const obligation: Obligation = {
  id: 7, projectId: 3, contractId: null, taskId: null, title: "Передать акт", status: "needs_confirmation",
  dueDate: "2026-09-07", dueTime: "12:00:00", timezone: "Europe/Moscow", resultNote: null,
  sourceType: "evidence", sourceName: "Договор.pdf", sourceExcerpt: "п. 5.2", confidence: 0.76,
  recordVersion: 3, evidencePins: [evidence], reviewState: "needs_review", escalationLevel: 1, deadlinePolicy: null,
};
const risk: AttentionItem = { kind: "risk", entityType: "risk", entityId: 8, recordVersion: 2,
  title: "Риск задержки", priority: "high", dueAt: null, status: "needs_confirmation",
  explanation: "human_review_required", evidencePins: [evidence] };

afterEach(cleanup);

describe("MVP3 management panels", () => {
  it("shows loading, error and empty states explicitly", () => {
    const view = render(<AttentionPanel state="loading" items={[]} total={0} onSelect={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем");
    view.rerender(<AttentionPanel state="error" items={[]} total={0} error="Сеть недоступна" onSelect={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Сеть недоступна");
    view.rerender(<AttentionPanel state="empty" items={[]} total={0} onSelect={vi.fn()} />);
    expect(screen.getByText("Сейчас ничего не требует внимания")).toBeInTheDocument();
  });

  it("selects the exact attention item and shows no fake progress", () => {
    const onSelect = vi.fn();
    render(<AttentionPanel state="ready" items={[risk]} total={1} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Риск задержки/ }));
    expect(onSelect).toHaveBeenCalledWith(risk);
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("blocks obligation execution when confidence is low", () => {
    render(<ObligationDetailPanel obligation={obligation} history={[]} historyState="idle" mutationState="idle"
      onLoadHistory={vi.fn()} onTransition={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Низкая уверенность");
    expect(screen.getByRole("button", { name: "В работу" })).toBeDisabled();
    expect(screen.getByText("Доказательство ev-17")).toBeInTheDocument();
  });

  it("shows CAS conflict and requests an exact history", () => {
    const load = vi.fn();
    render(<ObligationDetailPanel obligation={{ ...obligation, confidence: 0.95, reviewState: "confirmed" }} history={[]}
      historyState="empty" mutationState="conflict" mutationMessage="Запись уже изменена другим пользователем."
      onLoadHistory={load} onTransition={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("изменена другим пользователем");
    fireEvent.click(screen.getByRole("button", { name: "Обновить историю" }));
    expect(load).toHaveBeenCalledWith(7);
  });

  it("renders immutable history versions", () => {
    render(<ObligationDetailPanel obligation={obligation} history={[{ sequence: 1, event: "confirmed",
      fromStatus: "needs_confirmation", toStatus: "confirmed", recordVersion: 4, reason: null,
      evidencePins: [evidence], occurredAt: "2026-09-05T10:00:00Z" }]} historyState="ready"
      mutationState="idle" onLoadHistory={vi.fn()} onTransition={vi.fn()} />);
    expect(screen.getByText(/needs_confirmation → confirmed · v4/)).toBeInTheDocument();
  });

  it("requires human review before closing a risk", () => {
    render(<RiskDecisionPanel item={risk} history={[]} historyState="idle" mutationState="idle"
      onLoadHistory={vi.fn()} onTransition={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Закрыть риск" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeEnabled();
  });

  it("fails closed when meeting proposal API is absent", () => {
    render(<MeetingProposalPanel state="unavailable" proposals={[]} onConfirm={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("не подключён к HTTP API");
    expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
  });

  it("confirms only the selected meeting proposal", () => {
    const onConfirm = vi.fn();
    const proposal = { kind: "task" as const, entityType: "obligation" as const, entityId: 9,
      recordVersion: 2, status: "needs_confirmation", reviewState: "needs_review", taskId: null };
    render(<MeetingProposalPanel state="ready" proposals={[proposal]} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(onConfirm).toHaveBeenCalledWith(proposal, true);
  });

  it("shows only confirmed digest facts and marks API gap", () => {
    render(<DeadlineDigestPanel deadlinePolicy={null} digestState={{ status: "deferred_quiet_hours",
      localDate: "2026-09-05", deferredUntil: "2026-09-06T08:00:00+03:00", notificationId: null,
      externalActionsCreated: false }} notifications={[]} configurationAvailable={false} />);
    expect(screen.getAllByRole("status")[0]).toHaveTextContent(/quiet-hours/);
    expect(screen.getByText("Не создавались")).toBeInTheDocument();
    expect(screen.getByText("Сводка отложена до окончания тихих часов")).toBeInTheDocument();
  });

  it("shows the exact durable digest job instead of a fabricated percentage", () => {
    render(<DeadlineDigestPanel deadlinePolicy={{ reminderDays: [7, 1], quietHours: { start: "20:00", end: "08:00" } }}
      digestState={null} digestJob={{ jobId: 41, status: "queued", externalActionsCreated: false }}
      notifications={[]} configurationAvailable />);
    expect(screen.getByText("Задание сводки № 41")).toBeInTheDocument();
    expect(screen.getByText("Состояние очереди: queued")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
