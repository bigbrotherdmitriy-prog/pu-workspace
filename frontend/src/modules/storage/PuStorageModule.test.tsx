import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { PuStorageModule } from "./PuStorageModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); mockApi.mockReset(); });

const listing = {
  files: [
    { id: "pu:/Док", name: "Док", is_folder: true, size: null, modified_time: "2026-10-01T10:00:00Z", mime_type: "inode/directory" },
    { id: "pu:/a.pdf", name: "a.pdf", is_folder: false, size: 2048, modified_time: "2026-10-02T10:00:00Z", mime_type: "application/pdf" },
  ],
  usage: { used_bytes: 2048, quota_bytes: 2147483648 },
};

it("offers to connect when storage is not connected, then reloads", async () => {
  let connected = false;
  mockApi.mockImplementation(async (path: string, init?: { method?: string }) => {
    if (init?.method === "POST") { connected = true; return {}; }
    if (!connected) throw new Error("404");
    return listing;
  });
  render(<PuStorageModule projectId={7} />);
  fireEvent.click(await screen.findByText("Подключить хранилище"));
  expect(await screen.findByText("a.pdf")).toBeTruthy();
});

it("lists files with quota and opens a folder", async () => {
  mockApi.mockResolvedValue(listing);
  render(<PuStorageModule projectId={7} />);
  expect(await screen.findByText("a.pdf")).toBeTruthy();
  expect(screen.getByText(/Занято/)).toBeTruthy();
  fireEvent.click(screen.getByText(/Док/));
  await waitFor(() => {
    expect(mockApi.mock.calls.some((c) => String(c[0]).includes("folder_id=pu%3A%2F%D0%94%D0%BE%D0%BA"))).toBe(true);
  });
});

it("uploads a file with name in query and shows 413 error", async () => {
  mockApi.mockResolvedValue(listing);
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 413 });
  vi.stubGlobal("fetch", fetchMock);
  render(<PuStorageModule projectId={7} />);
  await screen.findByText("a.pdf");
  const input = screen.getByLabelText("Выбрать файлы") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "doc.txt")] } });
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("/pu-storage/upload?parent_id=");
  expect(String(fetchMock.mock.calls[0][0])).toContain("name=doc.txt");
  expect((await screen.findByRole("alert")).textContent).toContain("не хватает места");
});

it("shows empty folder hint", async () => {
  mockApi.mockResolvedValue({ files: [], usage: listing.usage });
  render(<PuStorageModule projectId={7} />);
  expect(await screen.findByText(/Папка пуста/)).toBeTruthy();
});
