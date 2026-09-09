import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { SnapshotVirtualTree, TREE_ROW_HEIGHT, virtualWindow } from "./SnapshotVirtualTree";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
const apiMock = vi.mocked(api);

beforeEach(() => apiMock.mockReset());

describe("10k snapshot virtualization", () => {
  it("keeps the rendered window bounded at the beginning, middle and end", () => {
    for (const scrollTop of [0, 5000 * TREE_ROW_HEIGHT, 10000 * TREE_ROW_HEIGHT]) {
      const range = virtualWindow(10000, scrollTop);
      expect(range.start).toBeGreaterThanOrEqual(0);
      expect(range.end).toBeLessThanOrEqual(10000);
      expect(range.end - range.start).toBeLessThanOrEqual(18);
    }
  });

  it("drops an old snapshot response after the project scope changes", async () => {
    let resolveOld!: (value: unknown) => void;
    apiMock
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce({
        nodes: [{ id: 2, external_id: "new", parent_external_id: null, name: "New project",
          node_type: "file", analysis_state: "pending", availability: "available" }],
        next_cursor: null, has_more: false,
      });
    const view = render(<SnapshotVirtualTree projectId={1} snapshotId={10} />);
    fireEvent.click(screen.getByText(/Дерево снимка/));
    await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(1));

    view.rerender(<SnapshotVirtualTree projectId={2} snapshotId={20} />);
    await waitFor(() => expect(screen.getByText("Применить")).not.toBeDisabled());
    fireEvent.click(screen.getByText("Применить"));
    await screen.findByText("New project");
    await act(async () => resolveOld({
      nodes: [{ id: 1, external_id: "old", parent_external_id: null, name: "Old project",
        node_type: "file", analysis_state: "pending", availability: "available" }],
      next_cursor: null, has_more: false,
    }));
    expect(screen.queryByText("Old project")).toBeNull();
  });
});
