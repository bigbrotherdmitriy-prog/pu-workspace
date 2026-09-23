import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { EvidenceTrailPanel } from "./EvidenceTrailPanel";

vi.mock("../../api/client", () => ({ api: vi.fn() }));
const mockedApi = vi.mocked(api);

const item = (id: string, authorization: "AUTO" | "CONFIRM" | "UNKNOWN", linkage: "typed" | "legacy_unlinked" = "typed") => ({
  id,
  occurred_at: "2026-09-23T12:00:00Z",
  phase: "OUTCOME",
  event: id,
  subject: { type: "action", id },
  linkage,
  authorization,
  outcome: "APPLIED",
  actor: { kind: "user", id: "1" },
  source_refs: [{ type: "message", id: "44" }],
  evidence_refs: [{ type: "evidence", id: "e-1", revision: 2 }],
  relation_refs: [],
});

describe("EvidenceTrailPanel", () => {
  beforeEach(() => mockedApi.mockReset());
  afterEach(cleanup);

  it("loads the project-scoped trail and distinguishes AUTO, CONFIRM and incomplete legacy linkage", async () => {
    mockedApi.mockResolvedValue({
      project_id: 17,
      items: [item("auto-event", "AUTO"), item("confirm-event", "CONFIRM"), item("legacy-event", "UNKNOWN", "legacy_unlinked")],
      next_cursor: null,
    });
    render(<EvidenceTrailPanel projectId={17} />);

    expect(await screen.findByText("auto-event")).toBeInTheDocument();
    expect(screen.getByText("confirm-event")).toBeInTheDocument();
    expect(screen.getByText("legacy-event")).toBeInTheDocument();
    expect(screen.getByText("legacy · связь неполная")).toBeInTheDocument();
    expect(screen.getAllByText("AUTO")).toHaveLength(1);
    expect(screen.getAllByText("CONFIRM")).toHaveLength(1);
    expect(mockedApi).toHaveBeenCalledWith(
      "/api/v54/projects/17/evidence-trail?limit=50",
      { cache: "no-store" },
    );
    expect(mockedApi.mock.calls.every(([url]) => !String(url).includes("/evidence/") && !String(url).includes("fragment"))).toBe(true);
  });

  it("follows the opaque cursor and appends the next page", async () => {
    mockedApi
      .mockResolvedValueOnce({ project_id: 17, items: [item("first", "AUTO")], next_cursor: "opaque+/=" })
      .mockResolvedValueOnce({ project_id: 17, items: [item("second", "CONFIRM")], next_cursor: null });
    render(<EvidenceTrailPanel projectId={17} />);
    fireEvent.click(await screen.findByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("second")).toBeInTheDocument();
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(mockedApi.mock.calls[1][0]).toContain("cursor=opaque%2B%2F%3D");
  });

  it("does not render a response from the project selected previously", async () => {
    let resolveFirst!: (value: unknown) => void;
    mockedApi
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ project_id: 18, items: [item("new-project", "CONFIRM")], next_cursor: null });
    const view = render(<EvidenceTrailPanel projectId={17} />);
    await waitFor(() => expect(mockedApi).toHaveBeenCalledTimes(1));
    view.rerender(<EvidenceTrailPanel projectId={18} />);
    expect(await screen.findByText("new-project")).toBeInTheDocument();
    resolveFirst({ project_id: 17, items: [item("old-project-secret", "AUTO")], next_cursor: null });
    await waitFor(() => expect(screen.queryByText("old-project-secret")).not.toBeInTheDocument());
  });
});
