import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../../App";
import { api, ApiError } from "../../api/client";

vi.mock("../../api/client", async original => ({ ...await original<typeof import("../../api/client")>(), api: vi.fn() }));
const mockApi = vi.mocked(api);
let projectRows = [{ id: 1, name: "Persistent Project" }, { id: 2, name: "Новый проект" }];
const empty = {
  summary: { attention: 0, active: 0, failed: 0, dead_letter: 0 },
  documents: [], snapshots: [], tasks: [], risks: [], decisions: [], drafts: [], messages: [], proposals: [],
  contracts: [], members: [], logs: [], adapters: [], rules: [], contacts: [], obligations: [], meetings: [],
  notifications: [], sessions: [], candidates: [], baselines: [], budget: [], cash_flow: [],
};
beforeEach(() => {
  sessionStorage.clear(); sessionStorage.setItem("pu_active_project_id", "2");
  window.history.replaceState({}, "", "/");
  projectRows = [{ id: 1, name: "Persistent Project" }, { id: 2, name: "Новый проект" }];
  Element.prototype.scrollIntoView = vi.fn();
  mockApi.mockReset();
  mockApi.mockImplementation(async path => {
    if (path === "/projects/") return { projects: projectRows };
    if (path.endsWith("/google/status")) return { authorized: true };
    return empty;
  });
});
afterEach(cleanup);

it.each(["ready", "retrying"])("uses actual standardize status %s, not a fictitious new launch", async status => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("/source-folders/discover")) return { project_id: 2, provider: "google_drive", connection_id: "a", connection_row_id: 7,
      folder_id: "root", breadcrumbs: [], folders: [{ id: "opaque-C", name: "Папка", registered: true,
        is_primary: true, snapshot_status: "ready", snapshot_id: 31 }] };
    if (path.endsWith("/standardize")) return { snapshot_id: 31, session_id: 42, status, already_queued: true };
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать рабочую папку" }));
  fireEvent.click(await screen.findByRole("button", { name: "Создать копию и стандартизировать" }));
  await screen.findByText(status === "ready" ? "Папка: обработка завершена" : "Папка: задание уже существует (retrying)");
  expect(screen.queryByText(/стандартизация «Папка» запущены/)).not.toBeInTheDocument();
  expect(mockApi.mock.calls.filter(([path]) => path.endsWith("/standardize"))).toHaveLength(1);
});

it("shows a non-looping active-job conflict when preparing source analysis", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("/source-folders/discover")) return { project_id: 2, provider: "google_drive", connection_id: "a", connection_row_id: 7,
      folder_id: "root", breadcrumbs: [], folders: [{ id: "opaque-C", name: "Папка", registered: true,
        snapshot_status: "ready", snapshot_id: 31, analysis_result: { mode: "safe_copy" } }] };
    if (path.endsWith("/analyze")) throw new ApiError("Snapshot already has queued, running or retrying work; wait for completion", 409, "");
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать рабочую папку" }));
  fireEvent.click(await screen.findByRole("button", { name: "Подготовить стандарт рабочей папки" }));
  await screen.findByText(/Задание уже находится в очереди/);
  expect(mockApi.mock.calls.filter(([path]) => path.endsWith("/analyze"))).toHaveLength(1);
});

it("renders missing explicit project without visually selecting Persistent Project", async () => {
  projectRows = projectRows.slice(0, 1);
  render(<App />);
  await screen.findByText(/Проект №2 отсутствует в ответе сервера/);
  expect(screen.getByRole("combobox")).toHaveValue("2");
  expect(sessionStorage.getItem("pu_active_project_id")).toBe("2");
  expect(mockApi.mock.calls.some(([path]) => path === "/dashboard/project?project_id=1")).toBe(false);
});

it("does not interpret already_queued analysis as completed", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("/source-folders/discover")) return { project_id: 2, provider: "google_drive", connection_id: "a", connection_row_id: 7,
      folder_id: "root", breadcrumbs: [], folders: [{ id: "opaque-C", name: "Папка", registered: true,
        snapshot_status: "ready", snapshot_id: 31, analysis_result: { mode: "safe_copy" } }] };
    if (path.endsWith("/analyze")) return { snapshot_id: 31, status: "analyzing", already_queued: true };
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать рабочую папку" }));
  fireEvent.click(await screen.findByRole("button", { name: "Подготовить стандарт рабочей папки" }));
  await screen.findByText(/Готовится таблица «Было → Станет»/);
  expect(screen.queryByText(/обработка завершена/)).not.toBeInTheDocument();
  expect(mockApi.mock.calls.filter(([path]) => path.endsWith("/analyze"))).toHaveLength(1);
});

it("handles retry-build 409 without enqueue success or automatic retry", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, options) => {
    if (path.endsWith("/processing-queue")) return { ...empty, snapshots: [{ id: 31, status: "failed", analysis_status: "pending", retry_count: 0, analysis_retry_count: 0 }] };
    if (path.endsWith("/retry-build")) throw new ApiError("Snapshot already has queued, running or retrying work; wait for completion", 409, "");
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Настройки" }));
  fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
  await screen.findByText(/Задание уже находится в очереди/);
  expect(screen.queryByText(/Повтор снимка №31 поставлен в очередь/)).not.toBeInTheDocument();
  expect(mockApi.mock.calls.filter(([path]) => path.endsWith("/retry-build"))).toHaveLength(1);
});

it("uses the real App picker and ignores confirmation after the user changes project", async () => {
  let resolve!: (value: unknown) => void;
  const previous = mockApi.getMockImplementation()!;
  const context = { project_id: 2, provider: "google_drive", connection_id: "account-2", connection_row_id: 7 };
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("/source-folders/discover")) return { ...context, folder_id: "opaque-C",
      breadcrumbs: [{ id: "root", name: "Мой диск" }, { id: "opaque-C", name: "Проект / этап" }], folders: [] };
    if (path.includes("/snapshot-queue")) return new Promise(yes => { resolve = yes; });
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать рабочую папку" }));
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать текущую папку" }));
  await waitFor(() => expect(resolve).toBeDefined());
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "1" } });
  await waitFor(() => expect(sessionStorage.getItem("pu_active_project_id")).toBe("1"));
  const calls = mockApi.mock.calls.length;
  await act(async () => { resolve({ ...context, folder_id: "opaque-C", source_folder: "Проект / этап", id: 31, job_id: 42, status: "building" }); });
  expect(screen.getByRole("combobox")).toHaveValue("1");
  expect(screen.queryByRole("button", { name: "Выбрать текущую папку" })).not.toBeInTheDocument();
  expect(mockApi.mock.calls.slice(calls).some(([path]) => path === "/projects/" || path.includes("/projects/2/"))).toBe(false);
  expect(JSON.parse(sessionStorage.getItem("pu_storage_selection_v1:2")!).job_id).toBe(42);
});

it("does not invent progress for a building snapshot with no worker measurements", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("/source-folders/discover")) return { project_id: 2, provider: "google_drive", connection_id: "a", connection_row_id: 7,
      folder_id: "root", breadcrumbs: [{ id: "root", name: "Мой диск" }],
      folders: [{ id: "opaque-C", name: "Папка", registered: true, is_primary: true, analyzed: false, snapshot_status: "building", snapshot_id: 31 }] };
    return previous(path, options);
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать рабочую папку" }));
  await screen.findByLabelText("Обработка: процент не предоставлен сервером");
  expect(screen.queryByText("5%")).not.toBeInTheDocument();
  expect(screen.queryByText("10%")).not.toBeInTheDocument();
});
