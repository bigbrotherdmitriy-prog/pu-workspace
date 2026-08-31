import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectLaunchWizard } from "./ProjectLaunchWizard";

vi.mock("./useProjectLaunchReadiness", () => ({
  useProjectLaunchReadiness: () => ({
    error: "",
    reload: vi.fn(),
    state: {
      projectName: "Тестовый проект",
      sourceReady: false,
      documents: 0,
      analyzedDocuments: 0,
      contracts: 0,
      linkedContracts: 0,
      scheduleRows: 0,
      budgetRows: 0,
      cashFlowRows: 0,
      contacts: 0,
      confirmedContacts: 0,
      inboxMessages: 0,
    },
  }),
}));

describe("ProjectLaunchWizard", () => {
  it("opens the first incomplete project launch action", () => {
    const openSection = vi.fn();
    render(<ProjectLaunchWizard projectId={1} openSection={openSection} />);
    expect(screen.getByText("0%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Продолжить запуск" }));
    expect(openSection).toHaveBeenCalledWith("Рабочий центр", "source");
  });
});
