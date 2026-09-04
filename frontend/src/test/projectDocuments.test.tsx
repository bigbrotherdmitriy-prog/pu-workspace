import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { App } from "../App";

vi.mock("../api/client", () => ({ api: vi.fn() }));
afterEach(cleanup);

const documentA = {
  id: 101, name: "Synthetic A.txt", source: "local_upload", status: "analyzed",
  current_version: 1, summary: "Private-to-project-A synthetic content",
  versions: [], links: { tasks: 0, risks: 0, decisions: 0, drafts: 0 },
};
let detailResponse: () => Promise<typeof documentA>;

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem("pu_active_project_id", "1");
  detailResponse = async () => documentA;
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/auth/me") return { id: 1, role: "owner", full_name: "QA" };
    if (path === "/projects/") return { projects: [{ id: 1, name: "QA A" }, { id: 2, name: "QA B" }] };
    if (path === "/projects/1/documents/101") return detailResponse();
    if (/^\/projects\/\d+\/documents\?/.test(path)) return { documents: path.includes("/1/") ? [documentA] : [] };
    if (path.startsWith("/dashboard/project")) return { summary: { attention: 0, overdue_tasks: 0, overdue_obligations: 0 }, documents: [] };
    const emptyLists: Record<string, string> = {
      snapshots: "snapshots", tasks: "tasks", risks: "risks", decisions: "decisions",
      "response-drafts": "drafts", inbox: "messages", proposals: "proposals",
      contracts: "contracts", members: "members", audit: "logs", automations: "rules",
      "project-contacts": "contacts", obligations: "obligations", meetings: "meetings",
      notifications: "notifications", "document-candidates": "candidates",
    };
    for (const [segment, key] of Object.entries(emptyLists)) {
      if (path.split(/[/?]/).includes(segment)) return { [key]: [] };
    }
    if (path.startsWith("/integrations/")) return { adapters: [{ key: "local", provider: "local", capability: "storage", name: "Локальная рабочая папка", description: "Загрузка", available: true, connected: true, action: "local_upload" }] };
    return null;
  });
});

async function openDocuments() {
  render(<App />);
  await screen.findByRole("option", { name: "QA A" });
  fireEvent.click(screen.getByTitle("Документы"));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/projects/1/documents/101"));
}

function switchToB() {
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
}

describe("project document isolation", () => {
  it("opens the upload dialog directly from Integrations without redirecting to a hint", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "QA A" });
    fireEvent.click(screen.getByTitle("Интеграции"));
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить папку" }));
    expect(screen.getByRole("dialog", { name: "Загрузка документов" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Интеграции", level: 1 })).toBeInTheDocument();
    expect(screen.queryByText(/Нажмите «Загрузить рабочую папку»/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить и проанализировать (0)" })).toBeDisabled();
  });
  it("clears the previous detail immediately and restores only the chosen project's document", async () => {
    await openDocuments();
    await screen.findByText(documentA.summary);
    switchToB();
    expect(screen.queryByText(documentA.summary)).not.toBeInTheDocument();
    await screen.findByText("Документы не найдены");
    expect(screen.queryByRole("heading", { name: documentA.name })).not.toBeInTheDocument();
    expect(sessionStorage.getItem("pu_active_project_id")).toBe("2");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "1" } });
    await screen.findByText(documentA.summary);
  });

  it("ignores a document response that arrives after changing projects", async () => {
    let resolve!: (value: typeof documentA) => void;
    const pending = new Promise<typeof documentA>((done) => { resolve = done; });
    detailResponse = () => pending;
    await openDocuments();
    switchToB();
    await screen.findByText("Документы не найдены");
    await act(async () => { resolve(documentA); await pending; });
    expect(screen.queryByText(documentA.summary)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: documentA.name })).not.toBeInTheDocument();
  });
});
