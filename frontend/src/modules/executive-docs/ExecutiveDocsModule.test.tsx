import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ExecutiveDocsModule } from "./ExecutiveDocsModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows object readiness and missing items", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) {
      return { objects: [{
        object_name: "ДУБНА", percent: 40, required_total: 10, present: 4, draft: 1,
        missing: ["Технический отчёт"], recommended_missing: [], file_issues: [],
        empty_folders: [], duplicates: [], last_scan_at: null,
      }] };
    }
    if (path.endsWith("/sources")) return [{ id: 7, object_name: "ДУБНА", folder_name: "f", last_scan_at: null }];
    if (path.endsWith("/participants")) return [];
    if (path.includes("/sources/7/scan")) return {};
    return {};
  });

  render(<ExecutiveDocsModule projectId={3} />);
  expect(await screen.findByText("ДУБНА — готовность 40%")).toBeTruthy();
  expect(screen.getByText("Технический отчёт")).toBeTruthy();
});

it("scans the source of an object", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) return { objects: [{
      object_name: "ДУБНА", percent: 40, required_total: 10, present: 4, draft: 1,
      missing: [], recommended_missing: [], file_issues: [], empty_folders: [],
      duplicates: [], last_scan_at: null,
    }] };
    if (path.endsWith("/sources")) return [{ id: 7, object_name: "ДУБНА", folder_name: "f", last_scan_at: null }];
    if (path.endsWith("/participants")) return [];
    return {};
  });

  render(<ExecutiveDocsModule projectId={3} />);
  const btn = await screen.findByLabelText("Проверить ИД ДУБНА");
  fireEvent.click(btn);
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/3/executive-docs/sources/7/scan", expect.objectContaining({ method: "POST" })));
});

it("adds a participant", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) return { objects: [{
      object_name: "ДУБНА", percent: 40, required_total: 10, present: 4, draft: 1,
      missing: [], recommended_missing: [], file_issues: [], empty_folders: [],
      duplicates: [], last_scan_at: null,
    }] };
    if (path.endsWith("/sources")) return [];
    if (path.endsWith("/participants")) return [];
    return {};
  });

  render(<ExecutiveDocsModule projectId={3} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Участники" }));
  fireEvent.change(await screen.findByLabelText("ФИО"), { target: { value: "Иванов И.И." } });
  fireEvent.click(screen.getByText("Добавить участника"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/3/executive-docs/participants",
    expect.objectContaining({ method: "POST", body: expect.stringContaining("\"full_name\":\"Иванов И.И.\"") })));
  const call = mockApi.mock.calls.find((c) => c[0] === "/projects/3/executive-docs/participants" && (c[1] as RequestInit | undefined)?.method === "POST");
  expect(call).toBeTruthy();
  expect((call![1] as RequestInit).body).toContain("\"role\":\"customer_control\"");
});

it("checks the AOSR and lists missing fields", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) return { objects: [{
      object_name: "ДУБНА", percent: 40, required_total: 10, present: 4, draft: 1,
      missing: [], recommended_missing: [], file_issues: [], empty_folders: [],
      duplicates: [], last_scan_at: null,
    }] };
    if (path.endsWith("/sources")) return [];
    if (path.endsWith("/participants")) return [];
    if (path.endsWith("/aosr/check")) return { missing: ["Приказ о назначении"] };
    return {};
  });

  render(<ExecutiveDocsModule projectId={3} />);
  fireEvent.click(await screen.findByRole("tab", { name: "АОСР" }));
  fireEvent.click(screen.getByText("Проверить"));
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalledWith("/projects/3/executive-docs/aosr/check",
    expect.objectContaining({ method: "POST" })));
  expect(await screen.findByText("Не хватает:")).toBeTruthy();
  expect(screen.getByText("Приказ о назначении")).toBeTruthy();
});

it("uploads a missing document straight into the object folder", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) return { objects: [{
      object_name: "ДУБНА", percent: 40, required_total: 10, present: 4, draft: 1,
      missing: ["Технический отчёт"], recommended_missing: [], file_issues: [], empty_folders: [],
      duplicates: [], last_scan_at: null,
    }] };
    if (path.endsWith("/sources")) return [{ id: 7, object_name: "ДУБНА", folder_name: "f", last_scan_at: null }];
    if (path.endsWith("/participants")) return [];
    return {};
  });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);

  render(<ExecutiveDocsModule projectId={3} />);
  const input = await screen.findByLabelText("Выбрать файлы для ДУБНА");
  fireEvent.change(input, { target: { files: [new File(["x"], "Отчёт.pdf")] } });
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("/projects/3/executive-docs/sources/7/upload?name=");
  expect(await screen.findByText(/Загружено файлов: 1/)).toBeTruthy();
  vi.unstubAllGlobals();
});


it("downloads the cable journal draft", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/report")) return { objects: [] };
    if (path.endsWith("/sources")) return [];
    if (path.endsWith("/participants")) return [];
    return {};
  });
  const blob = new Blob(["x"]);
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => blob });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
  render(<ExecutiveDocsModule projectId={3} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Акты и протоколы" }));
  fireEvent.click(await screen.findByText("Скачать заготовку кабельного журнала"));
  await vi.waitFor(() => expect(String(fetchMock.mock.calls[0][0])).toContain("/projects/3/executive-docs/cable-journal.xlsx"));
  vi.unstubAllGlobals();
});
