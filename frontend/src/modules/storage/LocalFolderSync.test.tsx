import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { LocalFolderSync } from "./LocalFolderSync";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);

function fileHandle(name: string, content: string, modified = 1000) {
  const file = new File([content], name, { lastModified: modified });
  return { kind: "file" as const, name, getFile: async () => file };
}
function dirHandle(name: string, children: Record<string, unknown>) {
  return { kind: "directory" as const, name, entries: async function* () { for (const entry of Object.entries(children)) yield entry; } };
}

// Minimal in-memory IndexedDB stand-in: one object store with put/get by key.
function fakeIndexedDb() {
  const data = new Map<string, unknown>();
  const store = {
    put: (value: unknown, key: string) => { data.set(key, value); return {}; },
    get: (key: string) => {
      const request: { result?: unknown; onsuccess?: () => void } = {};
      setTimeout(() => { request.result = data.get(key); request.onsuccess?.(); }, 0);
      return request;
    },
  };
  const db = {
    createObjectStore: () => store,
    transaction: () => {
      const tx: { oncomplete?: () => void; objectStore: () => typeof store } = { objectStore: () => store };
      setTimeout(() => tx.oncomplete?.(), 0);
      return tx;
    },
  };
  return {
    open: () => {
      const request: { result: typeof db; onupgradeneeded?: () => void; onsuccess?: () => void } = { result: db };
      setTimeout(() => { request.onupgradeneeded?.(); request.onsuccess?.(); }, 0);
      return request;
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("indexedDB", fakeIndexedDb());
  localStorage.clear();
  mockApi.mockResolvedValue({ id: "pu:/Папка" });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as unknown as Record<string, unknown>).showDirectoryPicker; mockApi.mockReset(); });

it("explains when the browser cannot pick folders", () => {
  render(<LocalFolderSync projectId={3} folder="pu:/" onDone={() => undefined} />);
  expect(screen.getByText(/Chrome, Edge и Яндекс Браузере/)).toBeTruthy();
});

it("uploads new files, creates folders and skips unchanged files on the second run", async () => {
  const tree = dirHandle("Проект", { "a.txt": fileHandle("a.txt", "A"), "Док": dirHandle("Док", { "b.pdf": fileHandle("b.pdf", "BB") }), ".hidden": fileHandle(".hidden", "x") });
  (window as unknown as Record<string, unknown>).showDirectoryPicker = async () => tree;
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  const onDone = vi.fn();
  render(<LocalFolderSync projectId={3} folder="pu:/" onDone={onDone} />);
  fireEvent.click(screen.getByText("Синхронизировать папку с компьютера"));
  expect(await screen.findByText(/Передано: 2, без изменений: 0/)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls.every((c) => String(c[0]).includes("/pu-storage/upload?parent_id="))).toBe(true);
  expect(mockApi.mock.calls.some((c) => String(c[0]).endsWith("/pu-storage/folders"))).toBe(true);
  expect(onDone).toHaveBeenCalled();

  fireEvent.click(await screen.findByText("Обновить из «Проект»"));
  expect(await screen.findByText(/Передано: 0, без изменений: 2/)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("reports a file that does not fit into the quota and stops", async () => {
  const tree = dirHandle("P", { "a.txt": fileHandle("a.txt", "A"), "b.txt": fileHandle("b.txt", "B") });
  (window as unknown as Record<string, unknown>).showDirectoryPicker = async () => tree;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 413 }));
  render(<LocalFolderSync projectId={3} folder="pu:/" onDone={() => undefined} />);
  fireEvent.click(screen.getByText("Синхронизировать папку с компьютера"));
  expect((await screen.findByRole("alert")).textContent).toContain("не хватает места");
  await waitFor(() => expect(screen.getByText(/с ошибками: 1/)).toBeTruthy());
});
