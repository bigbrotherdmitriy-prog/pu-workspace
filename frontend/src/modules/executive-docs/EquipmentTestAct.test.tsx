import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EquipmentTestAct } from "./EquipmentTestAct";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("sends header and equipment and downloads the act", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(["x"]) });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
  render(<EquipmentTestAct projectId={3} objectName="ДУБНА" />);
  fireEvent.change(screen.getByLabelText("Оборудование 1"), { target: { value: "Насос" } });
  fireEvent.change(screen.getByLabelText("Итог 1"), { target: { value: "fail" } });
  fireEvent.click(screen.getByText("Скачать акт"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/projects/3/executive-docs/equipment-test-act.docx");
  const body = JSON.parse(String((init as { body: string }).body));
  expect(body.header.object_name).toBe("ДУБНА");
  expect(body.items[0]).toMatchObject({ name: "Насос", result: "fail" });
});

it("shows the server error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: "Добавьте хотя бы одно оборудование" }) }));
  render(<EquipmentTestAct projectId={3} />);
  fireEvent.click(screen.getByText("Скачать акт"));
  expect((await screen.findByRole("alert")).textContent).toContain("Добавьте хотя бы одно");
});
