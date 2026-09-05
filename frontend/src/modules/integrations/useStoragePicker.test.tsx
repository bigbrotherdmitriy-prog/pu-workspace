import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { requestedProjectId, useProjectSelection } from "../../context/useProjectSelection";
import { restoredStorageSelection, useStoragePicker, type PickerContext, type PickerFolder } from "./useStoragePicker";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: vi.fn() }));
const mockApi = vi.mocked(api);
const binding: PickerContext = { project_id: 2, provider: "google_drive", connection_id: "account-2", connection_row_id: 7 };
const folder: PickerFolder = { id: "opaque-C", name: "Новый проект", provider: "google_drive", registered: false };
function discovery(context = binding, id = "opaque-B", item = folder) {
  return { ...context, folder_id: id, folders: [item], breadcrumbs: [
    { id: context.provider === "yandex_disk" ? "disk:/" : "root", name: "Диск" },
    { id, name: "Родитель" },
  ] };
}
function confirmation(context = binding, item = folder) {
  return { ...context, folder_id: item.id, source_folder: item.name, id: 31, job_id: 42, status: "building" };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function mount() {
  const ref = { current: 2 };
  const hook = renderHook(({ id }) => useStoragePicker<PickerFolder>(id, ref), { initialProps: { id: 2 } });
  return { ...hook, ref };
}
beforeEach(() => { sessionStorage.clear(); mockApi.mockReset(); window.history.replaceState({}, "", "/"); });
afterEach(cleanup);

describe("project-scoped storage picker", () => {
  it.each(["google_drive", "yandex_disk"])("confirms nested %s folder with exact project and guards", async provider => {
    const context = { ...binding, provider };
    const chosen = { ...folder, provider, id: provider === "yandex_disk" ? "disk:/Заказчик/Этап/Проект #1?скидка=10%" : "opaque-C" };
    mockApi.mockResolvedValueOnce(discovery(context, provider === "yandex_disk" ? "disk:/Заказчик/Этап" : "opaque-B", chosen));
    const { result } = mount();
    await act(() => result.current.open(provider));
    expect(result.current.context).toEqual(context);
    const get = new URL(mockApi.mock.calls[0][0], "https://local.test");
    expect(get.searchParams.get("folder_id")).toBeNull();
    expect(get.searchParams.get("provider")).toBe(provider);
    mockApi.mockResolvedValueOnce(confirmation(context, chosen));
    await act(() => result.current.confirm(chosen));
    const [path, options] = mockApi.mock.calls[1];
    expect(path).toBe(`/projects/2/source-folders/${encodeURIComponent(chosen.id)}/snapshot-queue?provider=${provider}&connection_id=account-2`);
    expect(options?.method).toBe("POST");
    expect(result.current.selection?.job_id).toBe(42);
    expect(result.current.selection?.status).toBe("building");
    expect(result.current.busyFolder).toBe("");
    expect(mockApi.mock.calls.every(([path]) => !path.includes("/yandex/root"))).toBe(true);
  });

  it("does not apply discovery after switching projects, even before the rerender", async () => {
    const pending = deferred<ReturnType<typeof discovery>>();
    mockApi.mockReturnValueOnce(pending.promise);
    const { result, ref, rerender } = mount();
    let request!: Promise<void>;
    act(() => { request = result.current.open("google_drive"); });
    ref.current = 1;
    await act(async () => { pending.resolve(discovery()); await request; });
    expect(result.current.folders).toEqual([]);
    rerender({ id: 1 });
    expect(result.current.isOpen).toBe(false);
  });

  it("applies only the latest discovery when responses arrive in reverse order", async () => {
    mockApi.mockResolvedValueOnce(discovery());
    const { result } = mount();
    await act(() => result.current.open());
    const first = deferred<ReturnType<typeof discovery>>(), second = deferred<ReturnType<typeof discovery>>();
    mockApi.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    let a!: Promise<void>, b!: Promise<void>;
    act(() => { a = result.current.navigate("opaque-C"); b = result.current.navigate("root"); });
    await act(async () => { second.resolve(discovery(binding, "root")); await b; });
    await act(async () => { first.resolve(discovery(binding, "opaque-C")); await a; });
    expect(result.current.folderId).toBe("root");
    expect(result.current.breadcrumbs.at(-1)?.id).toBe("root");
  });

  it.each(["close", "new-provider"])("discards late discovery after %s", async action => {
    const pending = deferred<ReturnType<typeof discovery>>();
    mockApi.mockReturnValueOnce(pending.promise);
    const { result } = mount();
    let old!: Promise<void>;
    act(() => { old = result.current.open("google_drive"); });
    if (action === "close") act(() => result.current.close());
    else {
      mockApi.mockResolvedValueOnce(discovery({ ...binding, provider: "yandex_disk" }, "disk:/"));
      await act(() => result.current.open("yandex_disk"));
    }
    await act(async () => { pending.resolve(discovery()); await old; });
    expect(result.current.context?.provider).not.toBe("google_drive");
    if (action === "close") expect(result.current.isOpen).toBe(false);
  });

  it("late confirmation retains IDs for its own project without reopening or requesting an old load", async () => {
    mockApi.mockResolvedValueOnce(discovery());
    const { result, ref, rerender } = mount();
    await act(() => result.current.open());
    const pending = deferred<ReturnType<typeof confirmation>>();
    mockApi.mockReturnValueOnce(pending.promise);
    let sent!: Promise<boolean>;
    act(() => { sent = result.current.confirm(folder); });
    ref.current = 1; rerender({ id: 1 });
    await act(async () => { pending.resolve(confirmation()); expect(await sent).toBe(false); });
    expect(ref.current).toBe(1);
    expect(result.current.isOpen).toBe(false);
    expect(result.current.selection).toBeNull();
    expect(restoredStorageSelection(2)?.job_id).toBe(42);
    expect(restoredStorageSelection(1)).toBeNull();
    expect(mockApi).toHaveBeenCalledTimes(2);
  });

  it("refuses confirmation from an old picker after switching the active ref", async () => {
    mockApi.mockResolvedValueOnce(discovery());
    const { result, ref } = mount();
    await act(() => result.current.open());
    ref.current = 1;
    await act(async () => { expect(await result.current.confirm(folder)).toBe(false); });
    expect(mockApi).toHaveBeenCalledTimes(1);
  });

  it.each(["provider", "connection_id", "connection_row_id", "project_id"] as const)("rejects discovery with mismatched %s", async field => {
    mockApi.mockResolvedValueOnce(discovery());
    const { result } = mount();
    await act(() => result.current.open("google_drive"));
    const changed = { ...binding, [field]: field === "project_id" || field === "connection_row_id" ? 99 : "different" };
    mockApi.mockResolvedValueOnce(discovery(changed));
    await act(() => result.current.navigate("opaque-C"));
    expect(result.current.needsReopen).toBe(true);
    expect(result.current.folders).toEqual([]);
    expect(result.current.error).toContain("Переоткройте");
  });

  it("shows wrong provider / 409 without automatically switching storage", async () => {
    mockApi.mockRejectedValueOnce(new ApiError("provider changed", 409, "test"));
    const { result } = mount();
    await act(() => result.current.open("google_drive"));
    expect(result.current.needsReopen).toBe(true);
    expect(result.current.error).toContain("другой диск");
    expect(mockApi).toHaveBeenCalledTimes(1);
  });

  it.each(["response", "409"])("invalidates confirmation on %s conflict", async kind => {
    mockApi.mockResolvedValueOnce(discovery());
    const { result } = mount();
    await act(() => result.current.open());
    if (kind === "409") mockApi.mockRejectedValueOnce(new ApiError("changed", 409, "test"));
    else mockApi.mockResolvedValueOnce(confirmation({ ...binding, connection_id: "other" }));
    await act(() => result.current.confirm(folder));
    expect(result.current.needsReopen).toBe(true);
    expect(result.current.selection).toBeNull();
    expect(restoredStorageSelection(2)).toBeNull();
  });

  it("restores selection on remount and asks the backend for actual status", async () => {
    sessionStorage.setItem("pu_storage_selection_v1:2", JSON.stringify(confirmation()));
    const { result, unmount } = mount();
    expect(result.current.selection?.id).toBe(31);
    unmount();
    mockApi.mockResolvedValueOnce(discovery(binding, "opaque-C"))
      .mockResolvedValueOnce({ snapshots: [{ id: 31, project_id: 2, status: "failed", analysis_status: "pending", analysis_error: "Ошибка обработки" }] });
    const next = mount();
    await act(() => next.result.current.open());
    expect(mockApi.mock.calls[0][0]).toBe("/projects/2/source-folders/discover");
    expect(next.result.current.selection?.status).toBe("failed");
    expect(next.result.current.selection?.analysis_error).toBe("Ошибка обработки");
  });

  it("supports null connection ID without inventing a server guard", async () => {
    const context = { ...binding, connection_id: null };
    mockApi.mockResolvedValueOnce(discovery(context));
    const { result } = mount();
    await act(() => result.current.open());
    mockApi.mockResolvedValueOnce(confirmation(context));
    await act(() => result.current.confirm(folder));
    const query = new URL(mockApi.mock.calls[1][0], "https://local.test").searchParams;
    expect(query.has("connection_id")).toBe(false);
    expect(query.has("connection_row_id")).toBe(false);
    expect(result.current.context?.connection_id).toBeNull();
  });

  it("accepts creation of the connection row for Google OAuth-only projects", async () => {
    const context = { ...binding, connection_id: "google-token:8", connection_row_id: null };
    mockApi.mockResolvedValueOnce(discovery(context));
    const { result } = mount();
    await act(() => result.current.open());
    mockApi.mockResolvedValueOnce(confirmation({ ...context, connection_row_id: 12 }));
    await act(() => result.current.confirm(folder));
    expect(result.current.context?.connection_row_id).toBe(12);
    expect(result.current.busyFolder).toBe("");
  });
});

describe("explicit project restoration", () => {
  it("keeps a new project when an old Persistent Project is first", () => {
    const { result, unmount } = renderHook(useProjectSelection);
    act(() => result.current.rememberProject(2));
    expect(requestedProjectId([{ id: 1 }, { id: 2 }], result.current.projectIdRef.current)).toBe(2);
    unmount();
    const next = renderHook(useProjectSelection);
    expect(next.result.current.projectId).toBe(2);
    expect(() => requestedProjectId([{ id: 1 }], 2)).toThrow("выберите проект явно");
    expect(next.result.current.projectIdRef.current).toBe(2);
  });
});
