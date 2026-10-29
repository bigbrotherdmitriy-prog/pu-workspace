import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { HelpCenter, matchTopics } from "./HelpCenter";

afterEach(cleanup);

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
