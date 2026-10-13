import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MobileAutoConfirm } from "./MobileAutoConfirm";
import type { MailContract, MailMessage } from "./types";

afterEach(cleanup);

const message: MailMessage = {
  id: 101, project_id: 7, contract_id: null, thread_id: "thread-1", direction: "incoming",
  sender: { email: "client@example.invalid" }, to: [], cc: [], subject: "Акт", content: "Текст",
  received_at: "2026-10-13T10:00:00Z", status: "ready", context_confirmed: false, context_version: 3,
  auto_context_confirmation_state: "not_confirmed", attachments: [], drafts: [],
};
const contracts: MailContract[] = [
  { id: 11, number: "Д-11", title: "Фасад", project_id: 7 },
  { id: 12, number: "Д-12", title: "Кровля", project_id: 7 },
  { id: 99, number: "Д-99", title: "Чужой проект", project_id: 8 },
];

function setup(overrides: Partial<Parameters<typeof MobileAutoConfirm>[0]> = {}) {
  const client = {
    confirmContext: vi.fn().mockResolvedValue({ ...message, contract_id: 12, context_confirmed: true, context_version: 4 }),
    confirmContextForAuto: vi.fn().mockResolvedValue({ message_id: 101, state: "confirmed_current", already_confirmed: false }),
  };
  const props = { message, contracts, client, onDone: vi.fn(), onNotice: vi.fn(), onError: vi.fn(), ...overrides };
  render(<MobileAutoConfirm {...props} />);
  return { ...props, client: props.client as typeof client };
}

describe("MobileAutoConfirm", () => {
  it("links the chosen contract, then confirms AUTO with the new context version", async () => {
    const { client, onDone, onNotice } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить для AUTO" }));
    const dialog = screen.getByRole("dialog", { name: "Договор письма для AUTO" });
    // Only contracts of the letter's project are offered.
    expect(screen.queryByText("Чужой проект")).toBeNull();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Кровля/ }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(client.confirmContextForAuto).toHaveBeenCalledWith(101, 7, 12, 4));
    expect(client.confirmContext).toHaveBeenCalledWith(101, 7, 12, 3);
    expect(client.confirmContext.mock.invocationCallOrder[0]).toBeLessThan(client.confirmContextForAuto.mock.invocationCallOrder[0]);
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onNotice).toHaveBeenCalledWith("Договор и контекст письма подтверждены для AUTO");
    expect(dialog).not.toBeInTheDocument();
  });

  it("preselects the contract already linked to the letter", () => {
    setup({ message: { ...message, contract_id: 11, context_confirmed: true } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить для AUTO" }));
    expect(screen.getByRole("radio", { name: /Фасад/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeEnabled();
  });

  it("does not ask for AUTO when linking the contract fails", async () => {
    const client = {
      confirmContext: vi.fn().mockRejectedValue(new Error("context_version_conflict")),
      confirmContextForAuto: vi.fn(),
    };
    const { onError, onDone } = setup({ client });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить для AUTO" }));
    fireEvent.click(screen.getByRole("radio", { name: /Фасад/ }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("context_version_conflict"));
    expect(client.confirmContextForAuto).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("context_version_conflict");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("stops when the server did not keep the chosen contract", async () => {
    const client = {
      confirmContext: vi.fn().mockResolvedValue({ ...message, contract_id: null, context_version: 4 }),
      confirmContextForAuto: vi.fn(),
    };
    const { onError } = setup({ client });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить для AUTO" }));
    fireEvent.click(screen.getByRole("radio", { name: /Фасад/ }));
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(client.confirmContextForAuto).not.toHaveBeenCalled();
  });

  it("is disabled for read-only letters and explains a project without contracts", () => {
    setup({ message: { ...message, ordinary_read_only: true } });
    expect(screen.getByRole("button", { name: "Подтвердить для AUTO" })).toBeDisabled();
    cleanup();
    setup({ contracts: [] });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить для AUTO" }));
    expect(screen.getByText(/нет договоров/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
  });
});
