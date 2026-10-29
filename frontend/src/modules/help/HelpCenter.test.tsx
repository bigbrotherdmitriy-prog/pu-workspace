import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { HelpCenter, matchTopics } from "./HelpCenter";

vi.mock("../../api/client", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../api/client")>()), api: vi.fn() }));
const mockApi = vi.mocked(api);
afterEach(() => { cleanup(); mockApi.mockReset(); try { localStorage.clear(); } catch { /* ok */ } });

it("opens with the topic of the active screen", () => {
  render(<HelpCenter active="Закупка" />);
  fireEvent.click(screen.getByLabelText("Справка и справочник"));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.getAllByText("Закупка").length).toBeGreaterThan(0);
  expect(screen.getByText("Как сделать")).toBeTruthy();
});

it("search finds topics and opens one", () => {
  render(<HelpCenter active="Проекты" />);
  fireEvent.click(screen.getByLabelText("Справка и справочник"));
  fireEvent.change(screen.getByLabelText("Поиск по справочнику"), { target: { value: "оборудование" } });
  const items = screen.getAllByRole("button").filter((b) => b.textContent?.includes("Оборудование"));
  expect(items.length).toBeGreaterThan(0);
  fireEvent.click(items[0]);
  expect(screen.getByText("Как сделать")).toBeTruthy();
});

it("matchTopics is case-insensitive and empty query returns all", () => {
  expect(matchTopics("").length).toBeGreaterThanOrEqual(18);
  expect(matchTopics("ЗАКУПКА").length).toBeGreaterThan(0);
  expect(matchTopics("zzzzqq")).toEqual([]);
});

it("closes", () => {
  render(<HelpCenter active="Проекты" />);
  fireEvent.click(screen.getByLabelText("Справка и справочник"));
  fireEvent.click(screen.getByLabelText("Закрыть справочник"));
  expect(screen.queryByRole("dialog")).toBeNull();
});


const onboarding = { done: 1, total: 3, percent: 33, next: "dds", items: [
  { key: "contract", title: "Загрузите договор", done: true, screen: "Договоры", hint: "h1" },
  { key: "dds", title: "Загрузите плановый ДДС", done: false, screen: "ГПР и ДДС", hint: "h2" },
  { key: "rd", title: "Загрузите тома рабочей документации", done: false, screen: "Рабочая документация", hint: "h3" },
] };

it("marks the help button for a new project and opens the checklist on click", async () => {
  mockApi.mockResolvedValue(onboarding);
  const go = vi.fn();
  render(<HelpCenter active="Проекты" projectId={7} onNavigate={go} />);
  await screen.findByLabelText("Есть рекомендации для начала работы");
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByLabelText("Справка и справочник"));
  expect(await screen.findByText(/Готово 1 из 3/)).toBeTruthy();
  fireEvent.click(screen.getAllByText(/Перейти: ГПР и ДДС/)[0]);
  expect(go).toHaveBeenCalledWith("ГПР и ДДС");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("does not mark the button again after the checklist was seen", async () => {
  localStorage.setItem("pu-onboarding-seen:7", "1");
  mockApi.mockResolvedValue(onboarding);
  render(<HelpCenter active="Проекты" projectId={7} />);
  await vi.waitFor(() => expect(mockApi).toHaveBeenCalled());
  expect(screen.queryByLabelText("Есть рекомендации для начала работы")).toBeNull();
  fireEvent.click(screen.getByLabelText("Справка и справочник"));
  fireEvent.click(screen.getByRole("tab", { name: "С чего начать" }));
  expect(await screen.findByText(/Готово 1 из 3/)).toBeTruthy();
});
