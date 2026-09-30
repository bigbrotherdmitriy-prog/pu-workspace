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
});
