import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmailCompensationCard, type EmailCompensationOffer } from "./EmailCompensationCard";


const available: EmailCompensationOffer = {
  direct_undo_possible: false,
  message: "Отменить отправку нельзя",
  status: "AVAILABLE",
  can_propose: true,
  source_action_id: "source-action-opaque",
  source_revision: 1,
  source_etag: "a".repeat(64),
  approval_mode: "CONFIRM",
};

afterEach(cleanup);


describe("EmailCompensationCard", () => {
  it("states that sent email cannot be undone and offers a safe correction", async () => {
    const onPropose = vi.fn().mockResolvedValue(undefined);
    render(<EmailCompensationCard offer={available} onPropose={onPropose} />);

    expect(screen.getByText("Отменить отправку нельзя")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Подготовить корректирующий ответ" }));

    await waitFor(() => expect(onPropose).toHaveBeenCalledWith(available));
  });

  it("fails closed when the server source binding is unavailable", () => {
    const onPropose = vi.fn().mockResolvedValue(undefined);
    render(<EmailCompensationCard offer={{
      direct_undo_possible: false,
      message: "Отменить отправку нельзя",
      status: "UNAVAILABLE",
      can_propose: false,
      unavailable_reason: "source_stale",
    }} onPropose={onPropose} />);

    expect(screen.getByRole("button", { name: "Подготовить корректирующий ответ" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("устарело");
  });

  it("shows a draft-only proposal and mandatory separate confirmation", () => {
    render(<EmailCompensationCard offer={{
      ...available,
      status: "PROPOSED",
      can_propose: false,
      proposal: {
        action_id: "corrective-action-opaque",
        revision: 1,
        state: "PROPOSED",
        ledger_state: "FROZEN",
        approval_mode: "CONFIRM",
        draft_id: 42,
      },
    }} onPropose={vi.fn()} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("отдельное подтверждение");
  });
});
