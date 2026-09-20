import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AttentionPanel } from "./AttentionPanel";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({ api: vi.fn() }));

const mockedApi = vi.mocked(api);

describe("AttentionPanel", () => {
  beforeEach(() => mockedApi.mockReset());
  afterEach(cleanup);

  it("loads permission-filtered rows, exposes exact source and navigates", async () => {
    mockedApi.mockResolvedValue({ items: [{
      kind: "meeting_proposal", entity_id: 7, status: "proposed", priority: "high",
      project_id: 4, title: "Подтвердить поручение", explanation: "Ожидает manager",
      origin: { type: "meeting_source", id: "source-1", source_version_id: "version-2" },
      navigation: { section: "Совещания", project_id: 4, entity_type: "meeting_proposal", entity_id: 7 },
    }], count: 1, next_cursor: "next-page" });
    const onOpen = vi.fn();
    render(<AttentionPanel projectId={4} onOpenSection={onOpen} />);

    expect(await screen.findByText("Подтвердить поручение")).toBeInTheDocument();
    expect(screen.getByText(/source-1.*version-2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Открыть/ }));
    expect(onOpen).toHaveBeenCalledWith("Совещания");
    expect(mockedApi.mock.calls[0][0]).toContain("project_id=4");
  });

  it("applies filters on the server", async () => {
    mockedApi
      .mockResolvedValueOnce({ items: [], count: 0 })
      .mockResolvedValueOnce({ items: [], count: 0 });
    render(<AttentionPanel projectId={3} onOpenSection={vi.fn()} />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("Тип"), { target: { value: "risk" } });
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(2));
    expect(mockedApi.mock.calls[1][0]).toContain("kind=risk");
  });

  it("follows the opaque cursor without interpreting it", async () => {
    mockedApi
      .mockResolvedValueOnce({ items: [], count: 0, next_cursor: "opaque+/=" })
      .mockResolvedValueOnce({ items: [], count: 0 });
    render(<AttentionPanel projectId={3} onOpenSection={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Показать ещё" }));
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(2));
    expect(mockedApi.mock.calls[1][0]).toContain("cursor=opaque%2B%2F%3D");
  });

  it("does not show a response that arrived after the project changed", async () => {
    let resolveFirst!: (value: unknown) => void;
    mockedApi
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ items: [], count: 0 });
    const view = render(<AttentionPanel projectId={1} onOpenSection={vi.fn()} />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(1));
    view.rerender(<AttentionPanel projectId={2} onOpenSection={vi.fn()} />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(2));
    resolveFirst({ items: [{
      kind: "risk", entity_id: 99, status: "confirmed", priority: "high",
      project_id: 1, title: "Скрытая старая запись", explanation: "old",
      origin: { type: "message", id: "old" },
      navigation: { section: "Риски и решения", project_id: 1, entity_type: "risk", entity_id: 99 },
    }], count: 1 });
    await waitFor(() => expect(screen.queryByText("Скрытая старая запись")).not.toBeInTheDocument());
  });
});
