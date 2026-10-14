import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MobileContextEditor } from "./MobileContextEditor";
import type { MailContract, MailMessage } from "./types";

afterEach(cleanup);

const message: MailMessage = {
  id: 101, project_id: 7, contract_id: 11, thread_id: "thread-1", direction: "incoming",
  sender: { email: "client@example.invalid" }, to: [], cc: [], subject: "Акт", content: "Текст",
  received_at: "2026-10-13T10:00:00Z", status: "ready", context_confirmed: true, context_version: 3,
  auto_context_confirmation_state: "not_confirmed", attachments: [], drafts: [],
};
const projects = [{ id: 7, name: "Дубна" }, { id: 8, name: "Городец" }];
const contracts: MailContract[] = [
  { id: 11, number: "Д-11", title: "Фасад", project_id: 7 },
  { id: 12, number: "Д-12", title: "Кровля", project_id: 7 },
  { id: 99, number: "Д-99", title: "Чужой проект", project_id: 8 },
];

function setup(overrides: Partial<Parameters<typeof MobileContextEditor>[0]> = {}) {
  const client = { confirmContext: vi.fn().mockResolvedValue({ ...message, contract_id: 12, context_version: 4 }) };
  const props = { message, projects, contracts, client, onDone: vi.fn(), onNotice: vi.fn(), onError: vi.fn(), ...overrides };
  render(<MobileContextEditor {...props} />);
  return { ...props, client: props.client as typeof client };
}

describe("MobileContextEditor", () => {
  it("opens with the current link and saves the new contract through confirm-context", async () => {
    const { client, onDone, onNotice } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Изменить связь" }));
    const sheet = screen.getByRole("dialog", { name: "Связь письма" });
    expect(within(sheet).getByLabelText("Проект")).toHaveValue("7");
    expect(within(sheet).getByLabelText("Договор")).toHaveValue("11");
    expect(within(sheet).queryByText(/Чужой проект/)).toBeNull();
    fireEvent.change(within(sheet).getByLabelText("Договор"), { target: { value: "12" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(client.confirmContext).toHaveBeenCalledWith(101, 7, 12, 3));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onNotice).toHaveBeenCalledWith("Проект и договор письма подтверждены");
    expect(sheet).not.toBeInTheDocument();
  });

  it("switching the project resets the contract to «Без договора»", async () => {
    const { client } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Изменить связь" }));
    const sheet = screen.getByRole("dialog");
    fireEvent.change(within(sheet).getByLabelText("Проект"), { target: { value: "8" } });
    expect(within(sheet).getByLabelText("Договор")).toHaveValue("0");
    fireEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(client.confirmContext).toHaveBeenCalledWith(101, 8, null, 3));
  });

  it("keeps the sheet open with the error when the save is rejected", async () => {
    const client = { confirmContext: vi.fn().mockRejectedValue(new Error("context_version_conflict")) };
    const { onError, onDone } = setup({ client, message: { ...message, context_confirmed: false } });
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить связь" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("context_version_conflict"));
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("context_version_conflict");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
