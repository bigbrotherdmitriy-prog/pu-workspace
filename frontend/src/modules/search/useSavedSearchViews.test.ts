import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api/client";
import { EMPTY_SEARCH_FILTERS } from "./useProjectSearch";
import { draftFromSavedView, savedViewPayload, useSavedSearchViews, type SavedSearchView } from "./useSavedSearchViews";

vi.mock("../../api/client", async (load) => {
  const actual = await load<typeof import("../../api/client")>();
  return { ...actual, api: vi.fn() };
});
const mockedApi = vi.mocked(api);
const view: SavedSearchView = {
  id: 4, project_id: 7, name: "Срочные задачи", record_version: 2,
  filters: { q: "срочно", types: ["task"], date_from: "2026-09-01", contract_id: 12 },
};

beforeEach(() => mockedApi.mockReset());

describe("saved search views", () => {
  it("maps drafts without leaking UI-only fields", () => {
    expect(savedViewPayload(7, " Мой вид ", { query: " акт ", filters: { ...EMPTY_SEARCH_FILTERS, types: ["task"], contractId: "12" } })).toEqual({
      project_id: 7, name: "Мой вид",
      filters: { q: "акт", types: ["task"], date_from: null, date_to: null, contract_id: 12, counterparty: null },
    });
    expect(draftFromSavedView(view)).toEqual({
      query: "срочно",
      filters: { types: ["task"], dateFrom: "2026-09-01", dateTo: "", contractId: "12", counterparty: "" },
    });
  });

  it("uses owner-scoped REST and CAS for rename and soft delete", async () => {
    mockedApi.mockResolvedValueOnce({ views: [view] }).mockResolvedValueOnce({ ...view, name: "Новое", record_version: 3 }).mockResolvedValueOnce({ id: 4, record_version: 4, deleted: true });
    const { result } = renderHook(() => useSavedSearchViews(7));
    await waitFor(() => expect(result.current.views).toHaveLength(1));
    await act(async () => { await result.current.rename(view, "Новое"); });
    expect(mockedApi.mock.calls[1]).toEqual(["/saved-search-views/4", expect.objectContaining({ method: "PATCH" })]);
    expect(JSON.parse(String(mockedApi.mock.calls[1][1]?.body))).toEqual({ name: "Новое", expected_record_version: 2 });
    await act(async () => { await result.current.remove({ ...view, record_version: 3 }); });
    expect(mockedApi.mock.calls[2][0]).toBe("/saved-search-views/4?expected_record_version=3");
  });

  it("reloads the current owner list after a CAS conflict", async () => {
    mockedApi.mockResolvedValueOnce({ views: [view] })
      .mockRejectedValueOnce(new ApiError("conflict", 409, "request"))
      .mockResolvedValueOnce({ views: [{ ...view, record_version: 3 }] });
    const { result } = renderHook(() => useSavedSearchViews(7));
    await waitFor(() => expect(result.current.views).toHaveLength(1));
    await expect(result.current.rename(view, "Новое")).rejects.toThrow("уже изменён");
    await waitFor(() => expect(result.current.views[0].record_version).toBe(3));
  });

  it("fails closed on project switch before the new list resolves", async () => {
    let resolveSecond!: (value: { views: SavedSearchView[] }) => void;
    mockedApi.mockResolvedValueOnce({ views: [view] }).mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    const { result, rerender } = renderHook(({ projectId }) => useSavedSearchViews(projectId), { initialProps: { projectId: 7 } });
    await waitFor(() => expect(result.current.views).toHaveLength(1));
    rerender({ projectId: 8 });
    expect(result.current.views).toEqual([]);
    await act(async () => { resolveSecond({ views: [{ ...view, project_id: 8 }] }); });
    expect(result.current.views[0].project_id).toBe(8);
  });
});
