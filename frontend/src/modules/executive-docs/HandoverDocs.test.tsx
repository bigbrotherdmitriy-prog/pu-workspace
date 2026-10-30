import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { HandoverDocs } from "./HandoverDocs";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); mockApi.mockReset(); });

it("prefills requisites and sends the object to the register download", async () => {
  mockApi.mockResolvedValue({ contractor: "ООО Монтаж", customer_rep: "Петров П.П." });
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: "Комплект ИД пуст" }) });
  vi.stubGlobal("fetch", fetchMock);
  render(<HandoverDocs projectId={4} objectName="ДУБНА" />);
  await waitFor(() => expect(screen.getAllByDisplayValue("ООО Монтаж").length).toBeGreaterThan(0));
  fireEvent.click(screen.getByText("Реестр ИД (Word)"));
  expect(await screen.findByText("Комплект ИД пуст")).toBeTruthy();
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/projects/4/executive-docs/id-register.docx");
  expect(JSON.parse(init.body).object_name).toBe("ДУБНА");
});
