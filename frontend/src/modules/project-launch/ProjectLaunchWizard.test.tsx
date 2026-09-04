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
  it("separates an existing folder import from a new managed project", () => {
    localStorage.clear();
    const openSection = vi.fn();
    render(<ProjectLaunchWizard projectId={1} storageAuthorized onConnectStorage={vi.fn()} openSection={openSection} />);
    expect(screen.getByText("Подключить готовую папку")).toBeInTheDocument();
    expect(screen.getByText("Создать постоянную структуру")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Выбрать существующий проект" }));
    expect(openSection).not.toHaveBeenCalled();
    expect(screen.getByText("ПОДТВЕРЖДЕНИЕ")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить и продолжить" }));
    expect(openSection).toHaveBeenCalledWith("Рабочий центр", "source");
  });

  it("routes to integrations before creating a workspace without storage authorization", () => {
    localStorage.clear();
    const openSection = vi.fn();
    const connectStorage = vi.fn();
    render(<ProjectLaunchWizard projectId={2} storageAuthorized={false} onConnectStorage={connectStorage} openSection={openSection} />);
    fireEvent.click(screen.getByRole("button", { name: "Выбрать новый проект" }));
    expect(screen.getByText(/подключить рабочее хранилище именно к этому проекту/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подключить хранилище к проекту" }));
    expect(connectStorage).toHaveBeenCalledOnce();
    expect(localStorage.getItem("pu-project-pending-start-mode:2")).toBe("managed");
    expect(openSection).not.toHaveBeenCalled();
  });
});
