import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { InsulationProtocol } from "./InsulationProtocol";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("sends header and measured pairs and downloads the file", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(["x"]) });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
  render(<InsulationProtocol projectId={3} objectName="ДУБНА" />);
  fireEvent.change(screen.getByLabelText("Линия 1"), { target: { value: "Л1" } });
  fireEvent.change(screen.getByLabelText("A-B линия 1"), { target: { value: "120" } });
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/projects/3/executive-docs/insulation-protocol.xlsx");
  const body = JSON.parse(String((init as { body: string }).body));
  expect(body.header.object_name).toBe("ДУБНА");
  expect(body.rows[0].line_name).toBe("Л1");
  expect(body.rows[0].pairs).toEqual({ "A-B": "120" });
});

it("shows the server error text", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: "Строка 1: отрицательное значение" }) }));
  render(<InsulationProtocol projectId={3} />);
  fireEvent.click(screen.getByText("Скачать протокол"));
  expect((await screen.findByRole("alert")).textContent).toContain("Строка 1");
});

it("adds and removes lines", () => {
  render(<InsulationProtocol projectId={3} />);
  fireEvent.click(screen.getByText("Добавить линию"));
  expect(screen.getByLabelText("Линия 2")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Убрать строку 2"));
  expect(screen.queryByLabelText("Линия 2")).toBeNull();
});
