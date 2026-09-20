import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import {
  EMPTY_SEARCH_FILTERS, projectSearchPath, toSearchHits, useProjectSearch,
  type ProjectSearchFilters,
} from "./useProjectSearch";

vi.mock("../../api/client", async (load) => {
  const actual = await load<typeof import("../../api/client")>();
  return { ...actual, api: vi.fn() };
});

const mockedApi = vi.mocked(api);
const response = (id: number, cursor: string | null = null) => ({
  items: [{
    entity_type: "task" as const, entity_id: id, name: `Задача ${id}`, date: "2026-09-25",
    project_id: 7, contract_id: 2, counterparty: "ООО Тест", status: "open",
    navigation: { section: "tasks", project_id: 7, entity_type: "task" as const, entity_id: id },
  }],
  next_cursor: cursor, limit: 50, scan_truncated: false, scan_cap_per_type: 1000,
  external_actions_created: false as const,
});

beforeEach(() => { mockedApi.mockReset(); });

describe("project search read model", () => {
  it("encodes the complete allowlisted filter contract", () => {
    const filters: ProjectSearchFilters = {
      types: ["document", "task"], dateFrom: "2026-09-01", dateTo: "2026-09-30",
      contractId: "2", counterparty: " ООО & Партнёр ",
    };
    const url = new URL(projectSearchPath(7, " ТЗ фасада ", filters, "opaque+cursor"), "https://local.test");
    expect(url.pathname).toBe("/project-search");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      project_id: "7", limit: "50", q: "ТЗ фасада", types: "document,task",
      date_from: "2026-09-01", date_to: "2026-09-30", contract_id: "2",
      counterparty: "ООО & Партнёр", cursor: "opaque+cursor",
    });
  });

  it("fails closed for wrong-project, malformed and effect-bearing rows", () => {
    expect(toSearchHits({ ...response(1), external_actions_created: true } as never, 7)).toEqual([]);
    expect(toSearchHits({ ...response(1), items: [{ ...response(1).items[0], project_id: 8 }] } as never, 7)).toEqual([]);
    expect(toSearchHits({ ...response(1), items: [{ ...response(1).items[0], navigation: { ...response(1).items[0].navigation, entity_id: 99 } }] } as never, 7)).toEqual([]);
  });

  it("debounces requests, appends cursor pages and does not leak stale project results", async () => {
    let resolveFirst!: (value: ReturnType<typeof response>) => void;
    mockedApi.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(response(2, null));
    const { result, rerender } = renderHook(({ projectId, query }) => useProjectSearch(projectId, query, EMPTY_SEARCH_FILTERS), {
      initialProps: { projectId: 7, query: "акт" },
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 280)); });
    expect(mockedApi).toHaveBeenCalledTimes(1);
    rerender({ projectId: 8, query: "новый" });
    expect(result.current.hits).toEqual([]);
    await act(async () => { resolveFirst(response(1)); await new Promise((resolve) => setTimeout(resolve, 280)); });
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(2));
    expect(result.current.hits).toEqual([]);
  });

  it("loads the next cursor without replacing earlier hits", async () => {
    mockedApi.mockResolvedValueOnce(response(1, "page-2")).mockResolvedValueOnce(response(2));
    const { result } = renderHook(() => useProjectSearch(7, "акт", EMPTY_SEARCH_FILTERS));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 280)); });
    await waitFor(() => expect(result.current.hits).toHaveLength(1));
    await act(async () => { await result.current.loadMore(); });
    expect(result.current.hits.map((item) => item.id)).toEqual([1, 2]);
    expect(new URL(mockedApi.mock.calls[1][0], "https://local.test").searchParams.get("cursor")).toBe("page-2");
  });
});
