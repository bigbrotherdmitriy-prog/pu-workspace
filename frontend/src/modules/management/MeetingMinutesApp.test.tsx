import { cleanup, configure, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../../App";
import { api } from "../../api/client";

vi.mock("../../api/client", async original => ({ ...await original<typeof import("../../api/client")>(), api: vi.fn() }));
const mockApi = vi.mocked(api);
configure({ asyncUtilTimeout: 5_000 });
vi.setConfig({ testTimeout: 15_000 });
const empty = {
  summary: { attention: 0, active: 0, failed: 0, dead_letter: 0 },
  documents: [], snapshots: [], tasks: [], risks: [], decisions: [], drafts: [], messages: [], proposals: [],
  contracts: [], members: [], logs: [], adapters: [], rules: [], contacts: [], obligations: [], meetings: [],
  notifications: [], sessions: [], candidates: [], baselines: [], budget: [], cash_flow: [],
};

beforeEach(() => {
  sessionStorage.clear(); sessionStorage.setItem("pu_active_project_id", "2");
  window.history.replaceState({}, "", "/");
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(window, "prompt").mockReturnValue("Synthetic meeting minutes");
  mockApi.mockReset();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([
  [{ proposal_state: "invalid_source", origin_status: "invalid_source", origin_reason: "meeting_source_binding_required", confirmation_available: false }, /Протокол сохранён.*привязка.*источнику/],
  [{ origin_status: "unknown_future", origin_reason: "raw-private-server-reason", confirmation_available: true }, /Протокол сохранён.*Подтверждение предложений недоступно/],
  [{ confirmation_available: false }, /Протокол сохранён.*Подтверждение предложений недоступно/],
])("reports source denial after minutes are saved %j", async (flags, notice) => {
  mockApi.mockImplementation(async (path, options) => {
    if (path === "/projects/") return { projects: [{ id: 2, name: "Synthetic Project" }] };
    if (path.endsWith("/google/status")) return { authorized: true };
    if (path === "/management/meetings/5" && options?.method === "PATCH") return {
      id: 5, status: "completed", tasks: 0, decisions: 0, ...flags,
    };
    if (path.startsWith("/management/meetings?")) return { meetings: [{ id: 5, project_id: 2,
      title: "Synthetic meeting", status: "planned", scheduled_at: null, agenda: null, minutes: null }] };
    return empty;
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Совещания" }));
  fireEvent.click(await screen.findByRole("button", { name: "Внести протокол и проанализировать" }));
  expect(await screen.findByText(notice)).toBeInTheDocument();
  expect(screen.queryByText("Протокол сохранён.")).not.toBeInTheDocument();
  expect(screen.queryByText(/raw-private-server-reason/)).not.toBeInTheDocument();
  expect(mockApi.mock.calls.filter(([path, options]) => path === "/management/meetings/5" && options?.method === "PATCH")).toHaveLength(1);
  expect(mockApi.mock.calls.some(([path]) => path.endsWith("/confirm"))).toBe(false);
});
