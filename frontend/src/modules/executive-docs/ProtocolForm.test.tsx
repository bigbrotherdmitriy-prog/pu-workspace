import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AVR_PROTOCOL, EARTHING_PROTOCOL, PHASE_ZERO_PROTOCOL, ProtocolForm } from "./ProtocolForm";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function okFetch() {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: async () => new Blob(["x"]) });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
  return fetchMock;
}

it("earthing: sends only filled cells and the default voltage", async () => {
  const fetchMock = okFetch();
  render(<ProtocolForm projectId={3} objectName="ДУБНА" config={EARTHING_PROTOCOL} />);
  fireEvent.change(screen.getByLabelText("Заземляющее устройство / точка 1"), { target: { value: "ЗУ-1" } });
  fireEvent.change(screen.getByLabelText("Измерено, Ом 1"), { target: { value: "3,2" } });
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/projects/3/executive-docs/earthing-protocol.xlsx");
  const body = JSON.parse(String((init as { body: string }).body));
  expect(body.header.object_name).toBe("ДУБНА");
  expect(body.rows[0]).toEqual({ point_name: "ЗУ-1", network_voltage_v: "380", measured_ohm: "3,2" });
});

it("phase-zero: posts the device type the server expects", async () => {
  const fetchMock = okFetch();
  render(<ProtocolForm projectId={3} config={PHASE_ZERO_PROTOCOL} />);
  fireEvent.change(screen.getByLabelText("Цепь / щит / приёмник 1"), { target: { value: "ЩР-1" } });
  fireEvent.change(screen.getByLabelText("Iном, А 1"), { target: { value: "16" } });
  fireEvent.change(screen.getByLabelText("Характеристика 1"), { target: { value: "C" } });
  fireEvent.change(screen.getByLabelText("Zпц, Ом 1"), { target: { value: "0,9" } });
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const body = JSON.parse(String((fetchMock.mock.calls[0][1] as { body: string }).body));
  expect(body.rows[0]).toMatchObject({ circuit_name: "ЩР-1", device_type: "автоматический выключатель", rated_a: "16", characteristic: "C", loop_ohm: "0,9" });
});

it("shows the server error and manages rows", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: "Строка 1: не указана цепь" }) }));
  render(<ProtocolForm projectId={3} config={PHASE_ZERO_PROTOCOL} />);
  fireEvent.click(screen.getByText("Добавить строку"));
  expect(screen.getByLabelText("Цепь / щит / приёмник 2")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Убрать строку 2"));
  expect(screen.queryByLabelText("Цепь / щит / приёмник 2")).toBeNull();
  fireEvent.click(screen.getByText("Скачать протокол"));
  expect((await screen.findByRole("alert")).textContent).toContain("Строка 1");
});

it("avr: sends scenario, times and the yes/no action", async () => {
  const fetchMock = okFetch();
  render(<ProtocolForm projectId={3} config={AVR_PROTOCOL} />);
  fireEvent.change(screen.getByLabelText("Сценарий 1"), { target: { value: "Пропадание напряжения на вводе 1" } });
  fireEvent.change(screen.getByLabelText("Измерено, с 1"), { target: { value: "0,6" } });
  fireEvent.change(screen.getByLabelText("Сработало как ожидалось 1"), { target: { value: "yes" } });
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(String(fetchMock.mock.calls[0][0])).toContain("/executive-docs/avr-protocol.xlsx");
  const body = JSON.parse(String((fetchMock.mock.calls[0][1] as { body: string }).body));
  expect(body.rows[0]).toEqual({ scenario: "Пропадание напряжения на вводе 1", measured_time_s: "0,6", action_ok: "yes" });
});

it("does not send untouched rows that only carry default values", async () => {
  const fetchMock = okFetch();
  render(<ProtocolForm projectId={3} config={PHASE_ZERO_PROTOCOL} />);
  fireEvent.change(screen.getByLabelText("Цепь / щит / приёмник 1"), { target: { value: "ЩР-1" } });
  fireEvent.click(screen.getByText("Добавить строку"));
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const body = JSON.parse(String((fetchMock.mock.calls[0][1] as { body: string }).body));
  expect(body.rows).toHaveLength(1);
  expect(body.rows[0].circuit_name).toBe("ЩР-1");
});

it("sends the CSRF token with the cookie session (plain fetch POST)", async () => {
  document.cookie = "pu_csrf=tok-123";
  const fetchMock = okFetch();
  render(<ProtocolForm projectId={3} config={EARTHING_PROTOCOL} />);
  fireEvent.change(screen.getByLabelText("Заземляющее устройство / точка 1"), { target: { value: "ЗУ-1" } });
  fireEvent.click(screen.getByText("Скачать протокол"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const headers = (fetchMock.mock.calls[0][1] as { headers: Record<string, string> }).headers;
  expect(headers["X-CSRF-Token"]).toBe("tok-123");
  document.cookie = "pu_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});
