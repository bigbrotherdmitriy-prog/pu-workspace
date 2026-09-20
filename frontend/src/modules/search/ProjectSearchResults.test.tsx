import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectSearchResults, ProjectSearchWorkspace, type ProjectSearchHit } from "./ProjectSearchResults";
import * as searchHook from "./useProjectSearch";
import * as savedHook from "./useSavedSearchViews";

const navigation = { section: "tasks", project_id: 7, entity_type: "task" as const, entity_id: 1 };
const duplicateTasks: ProjectSearchHit[] = [
  { id: 1, kind: "task", title: "Подготовить акт", detail: "open", navigation },
  { id: 1, kind: "task", title: "Другое представление той же задачи", detail: "open", navigation },
];

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("ProjectSearchResults", () => {
  it("deduplicates by stable entity identity and opens the represented hit", () => {
    const onOpen = vi.fn();
    render(<ProjectSearchResults query="акт" hits={duplicateTasks} onOpen={onOpen} scanTruncated />);
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByText("Просмотрен безопасно ограниченный объём данных. Уточните запрос или фильтры.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("listbox", { name: "Результаты поиска по проекту" }).querySelector("button")!);
    expect(onOpen).toHaveBeenCalledWith(duplicateTasks[0]);
  });

  it("shows loading, errors and cursor pagination explicitly", () => {
    const onLoadMore = vi.fn();
    const { rerender } = render(<ProjectSearchResults query="акт" hits={[]} loading onOpen={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Ищем по проекту");
    rerender(<ProjectSearchResults query="акт" hits={duplicateTasks.slice(0, 1)} error="Ошибка провайдера" hasMore onLoadMore={onLoadMore} onOpen={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ошибка провайдера");
    fireEvent.click(screen.getByText("Показать ещё"));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});

describe("ProjectSearchWorkspace", () => {
  beforeEach(() => {
    vi.spyOn(searchHook, "useProjectSearch").mockReturnValue({
      hits: duplicateTasks.slice(0, 1), loading: false, error: "", nextCursor: null,
      scanTruncated: false, loadMore: vi.fn(),
    });
    vi.spyOn(savedHook, "useSavedSearchViews").mockReturnValue({
      views: [{ id: 9, project_id: 7, name: "Мои задачи", filters: { q: "акт", types: ["task"] }, record_version: 3 }],
      loading: false, error: "", create: vi.fn().mockResolvedValue({ id: 10 }), rename: vi.fn(), remove: vi.fn(), reload: vi.fn(),
    });
  });

  it("focuses the command search on Ctrl+K and opens an exact server hit", async () => {
    const onOpen = vi.fn();
    render(<ProjectSearchWorkspace projectId={7} query="акт" onQueryChange={vi.fn()} onOpen={onOpen} />);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByLabelText("Поиск по проекту")).toHaveFocus();
    fireEvent.click(screen.getByRole("listbox", { name: "Результаты поиска по проекту" }).querySelector("button")!);
    expect(onOpen).toHaveBeenCalledWith(duplicateTasks[0]);
  });

  it("applies a saved view and exposes all eight server-side type filters", async () => {
    const onQueryChange = vi.fn();
    render(<ProjectSearchWorkspace projectId={7} query="" onQueryChange={onQueryChange} onOpen={vi.fn()} />);
    fireEvent.focus(screen.getByLabelText("Поиск по проекту"));
    fireEvent.change(screen.getByLabelText("Сохранённый вид"), { target: { value: "9" } });
    expect(onQueryChange).toHaveBeenCalledWith("акт");
    fireEvent.click(screen.getByLabelText("Фильтры поиска"));
    await waitFor(() => expect(screen.getAllByRole("checkbox")).toHaveLength(8));
    expect(screen.getByLabelText("Задача")).toBeChecked();
  });
});
