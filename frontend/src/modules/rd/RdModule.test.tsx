import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { RdModule } from "./RdModule";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.restoreAllMocks(); mockApi.mockReset(); });

const volumes = [
  {
    id: 11, title: "Том 1 — Электрика", file_name: "rd-1.pdf",
    section: "ЭО", code: "РД-01", object_name: "Корпус А",
    status: "parsed", page_count: 120, spec_pages: "5-8", error_message: null,
    counts: { proposed: 3, confirmed: 12, rejected: 1 },
  },
  {
    id: 12, title: "Том 2 — ВК", file_name: "rd-2.pdf",
    section: "ВК", code: "РД-02", object_name: null,
    status: "failed", page_count: null, spec_pages: null, error_message: "Не удалось распознать PDF",
    counts: { proposed: 0, confirmed: 0, rejected: 0 },
  },
];

it("shows volumes with statuses and counters", async () => {
  mockApi.mockResolvedValue(volumes);
  render(<RdModule projectId={5} />);
  expect(await screen.findByText(/Том 1 — Электрика/)).toBeTruthy();
  expect(screen.getByText("разобран")).toBeTruthy();
  expect(screen.getByText("ошибка")).toBeTruthy();
  expect(screen.getByText("3 / 12")).toBeTruthy();
  expect(screen.getByText("Не удалось распознать PDF")).toBeTruthy();
});

it("extracts folder id from URL and posts import", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/rd/import")) return { added: 2, existing: 1, volumes: [] };
    return volumes;
  });
  render(<RdModule projectId={5} />);
  await screen.findByText(/Том 1 — Электрика/);
  const input = screen.getByLabelText("Ссылка или ID папки на Google Диске") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://drive.google.com/drive/folders/ABC_123xyz?usp=sharing" } });
  fireEvent.click(screen.getByText("Загрузить тома"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/5/rd/import",
    expect.objectContaining({ method: "POST", body: expect.stringContaining('"folder_id":"ABC_123xyz"') }),
  ));
  expect(await screen.findByText("Добавлено: 2, уже было: 1")).toBeTruthy();
});

const items = [
  { id: 101, volume_id: 11, sort_order: 1, pos: "1", name: "Кабель ВВГ", type_mark: "ВВГ 3x2.5", unit: "м", qty: "150", note: "", source_page: 6, status: "proposed" },
  { id: 102, volume_id: 11, sort_order: 2, pos: "2", name: "Автомат", type_mark: "ВА47", unit: "шт", qty: "4", note: "", source_page: 7, status: "confirmed" },
];

it("opens volume and shows items", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/items")) return items;
    return volumes;
  });
  render(<RdModule projectId={5} />);
  await screen.findByText(/Том 1 — Электрика/);
  fireEvent.click(screen.getAllByText("Открыть")[0]);
  expect(await screen.findByText("Кабель ВВГ")).toBeTruthy();
  expect(screen.getByText("на проверке: 1 · подтверждено: 1")).toBeTruthy();
});

it("confirm button sends PATCH status confirmed", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/items")) return items;
    if (path.match(/\/rd\/items\/\d+$/)) return { ...items[0], status: "confirmed" };
    return volumes;
  });
  render(<RdModule projectId={5} />);
  await screen.findByText(/Том 1 — Электрика/);
  fireEvent.click(screen.getAllByText("Открыть")[0]);
  await screen.findByText("Кабель ВВГ");
  fireEvent.click(screen.getAllByText("Подтвердить")[0]);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/5/rd/items/101",
    expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"status":"confirmed"') }),
  ));
});

it("confirm all sends confirm-all", async () => {
  mockApi.mockImplementation(async (path: string) => {
    if (path.endsWith("/items")) return items;
    if (path.endsWith("/confirm-all")) return { confirmed: 1 };
    return volumes;
  });
  render(<RdModule projectId={5} />);
  await screen.findByText(/Том 1 — Электрика/);
  fireEvent.click(screen.getAllByText("Открыть")[0]);
  await screen.findByText("Кабель ВВГ");
  fireEvent.click(screen.getByText("Подтвердить всё"));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(
    "/projects/5/rd/volumes/11/confirm-all",
    expect.objectContaining({ method: "POST" }),
  ));
});

it("does not crash on empty list", async () => {
  mockApi.mockResolvedValue([]);
  render(<RdModule projectId={5} />);
  expect(await screen.findByText(/Томов пока нет/)).toBeTruthy();
});
