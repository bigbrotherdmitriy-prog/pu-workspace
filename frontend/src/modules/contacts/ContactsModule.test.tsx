import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ContactsModule } from "./ContactsModule";

vi.mock("../../api/client", async (original) => ({
  ...await original<typeof import("../../api/client")>(),
  api: vi.fn(),
}));

describe("ContactsModule conflict review", () => {
  it("resolves an exact versioned cross-project binding explicitly", async () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("Клиент подтвердил новый проект");
    vi.mocked(api)
      .mockResolvedValueOnce({ conflicts: [{
        id: 8, record_version: 3, contact_id: 4, contact_record_version: 5,
        contact_name: "Ирина", contact_email: "irina@example.test",
        current_project_id: 1, candidate_project_id: 2, status: "pending",
      }] })
      .mockResolvedValueOnce({ status: "resolved" })
      .mockResolvedValueOnce({ conflicts: [] });

    render(<ContactsModule projectId={2} contacts={[]} contracts={[]} drafts={[]}
      reload={reload} onNotice={vi.fn()} onError={vi.fn()}
      onUpdateDraft={vi.fn()} onSendDraft={vi.fn()} />);

    expect(await screen.findByText("irina@example.test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Перенести сюда" }));

    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/project-contacts/conflicts/8/resolve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          resolution: "move_to_candidate",
          reason: "Клиент подтвердил новый проект",
          expected_record_version: 3,
          expected_contact_record_version: 5,
        }),
      }),
    ));
    expect(reload).toHaveBeenCalledOnce();
    prompt.mockRestore();
  });
});
