import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { IdKit } from "./IdKit";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); mockApi.mockReset(); });

const proposal = {
  object_name: "ДУБНА", sections: ["ЭМ"], rd_items: 12, estimate_lines: 3,
  items: [
    { key: "a", title: "Протокол проверки заземляющего устройства", doc_kind: "protocol", section: "ЭМ", norm_ref: null, reason: "заземление: в РД 2 поз.", source: "rule", checked: true },
    { key: "b", title: "Журнал прокладки кабелей", doc_kind: "journal", section: "ЭМ", norm_ref: null, reason: "кабели: в РД 9 поз.", source: "rule", checked: true },
  ],
};

it("lets the user untick and add documents, then saves only the selection", async () => {
  mockApi.mockImplementation(async (_p: string, init?: { method?: string }) => (init?.method === "POST" ? { created: 2 } : proposal));
  const onSaved = vi.fn();
  render(<IdKit projectId={4} objectNames={["ДУБНА"]} onSaved={onSaved} />);
  expect(await screen.findByText(/Протокол проверки заземляющего устройства/)).toBeTruthy();
  expect(screen.getByText(/позиций РД — 12, строк смет — 3/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText(/Журнал прокладки кабелей/));
  fireEvent.change(screen.getByPlaceholderText(/Свой документ/), { target: { value: "Акт на ограждение" } });
  fireEvent.click(screen.getByText("Добавить"));
  fireEvent.click(screen.getByText("Создать комплект (2)"));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const post = mockApi.mock.calls.find((c) => (c[1] as { method?: string } | undefined)?.method === "POST")!;
  const sent = JSON.parse((post[1] as { body: string }).body);
  expect(sent.items.map((i: { title: string }) => i.title)).toEqual(["Протокол проверки заземляющего устройства", "Акт на ограждение"]);
  expect(sent.object_name).toBe("ДУБНА");
  expect(await screen.findByText(/Комплект создан: 2 документов/)).toBeTruthy();
});
