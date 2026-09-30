import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { YandexMailConnectionDialog } from "./YandexMailConnectionDialog";

afterEach(cleanup);

describe("YandexMailConnectionDialog", () => {
  it("collects an app password without exposing or retaining it", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<YandexMailConnectionDialog
      open
      initialEmail="mailbox@example.test"
      busy={false}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />);

    const password = screen.getByLabelText("Пароль приложения «Почта»");
    expect(password).toHaveAttribute("type", "password");
    expect(screen.getByText(/SMTP и AUTO не включаются/)).toBeInTheDocument();
    fireEvent.change(password, { target: { value: "app-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onSubmit).toHaveBeenCalledWith("mailbox@example.test", "app-password-123");
  });

  it("submits a password injected into the field by a password manager", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<YandexMailConnectionDialog
      open
      initialEmail="mailbox@example.test"
      busy={false}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />);

    const password = screen.getByLabelText("Пароль приложения «Почта»") as HTMLInputElement;
    const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    valueSetter?.call(password, "manager-filled-password");

    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onSubmit).toHaveBeenCalledWith("mailbox@example.test", "manager-filled-password");
  });

  it("shows a connection error inside the dialog and clears the rejected password", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("Яндекс отклонил авторизацию. Код обращения: test-request-id"));
    render(<YandexMailConnectionDialog
      open
      initialEmail="mailbox@example.test"
      busy={false}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />);

    const password = screen.getByLabelText("Пароль приложения «Почта»") as HTMLInputElement;
    fireEvent.change(password, { target: { value: "rejected-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("test-request-id");
    expect(password).toHaveValue("");
    expect(password).toHaveFocus();
  });
});
