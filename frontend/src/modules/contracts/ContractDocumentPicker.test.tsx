import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContractDocumentPicker } from "./ContractDocumentPicker";

describe("ContractDocumentPicker", () => {
  it("lets the user select an exact file before linking it", () => {
    const onLink = vi.fn();
    render(<ContractDocumentPicker
      contractId={15}
      open
      busy={false}
      tab="server"
      query=""
      documents={[
        { id: 1, name: "Приложение №6.docx", source: "google_drive_copy" },
        { id: 2, name: "Договор ГК-08-194.pdf", source: "google_drive_copy" },
      ]}
      candidates={[]}
      onToggle={vi.fn()}
      onSuggest={vi.fn()}
      onTabChange={vi.fn()}
      onQueryChange={vi.fn()}
      onLink={onLink}
    />);

    const linkButton = screen.getByRole("button", { name: "Привязать выбранный файл" });
    expect(linkButton).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Выбрать файл Договор ГК-08-194.pdf" }));
    expect(screen.getByText("Выбран: Договор ГК-08-194.pdf")).toBeInTheDocument();
    expect(linkButton).toBeEnabled();
    fireEvent.click(linkButton);
    expect(onLink).toHaveBeenCalledWith(2);
  });
});
