import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContractDocumentPicker } from "./ContractDocumentPicker";

describe("ContractDocumentPicker", () => {
  it("lets the user select an exact file before linking it", () => {
    const onLink = vi.fn();
    const onClose = vi.fn();
    render(<ContractDocumentPicker
      contractId={15}
      open
      busy={false}
      tab="server"
      query=""
      documents={[
        { id: 1, name: "Договоры", source: "google_drive_copy", external_id: "folder-contracts", mime_type: "application/vnd.google-apps.folder" },
        { id: 2, name: "ДЕМО-ДОКУМЕНТ-001.pdf", source: "google_drive_copy", parent_external_id: "folder-contracts" },
      ]}
      candidates={[]}
      onOpen={vi.fn()}
      onClose={onClose}
      onSuggest={vi.fn()}
      onTabChange={vi.fn()}
      onQueryChange={vi.fn()}
      onLink={onLink}
    />);

    const linkButton = screen.getByRole("button", { name: "Привязать выбранный файл" });
    const searchInput = screen.getByRole("textbox", { name: "Поиск документа по названию" });
    const placeholder = searchInput.getAttribute("placeholder") || "";
    expect(placeholder).toBe("Например: ДЕМО-ДОКУМЕНТ-001.pdf");
    const removedMarkers = [["ГК", "08", "194"], ["Налог", "Сервис"]].map((parts) => parts.join("-"));
    removedMarkers.forEach((marker) => expect(placeholder).not.toContain(marker));
    expect(linkButton).toBeDisabled();
    expect(screen.queryByRole("radio", { name: "Выбрать файл ДЕМО-ДОКУМЕНТ-001.pdf" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Договоры Открыть папку/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Выбрать файл ДЕМО-ДОКУМЕНТ-001.pdf" }));
    expect(screen.getByText("Выбран: ДЕМО-ДОКУМЕНТ-001.pdf")).toBeInTheDocument();
    expect(linkButton).toBeEnabled();
    fireEvent.click(linkButton);
    expect(onLink).toHaveBeenCalledWith(2);
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
