import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useManagementCenter } from "./useManagementCenter";

const attentionItem = { kind: "obligation_review", entity_type: "obligation", entity_id: 7, record_version: 3,
  title: "Передать акт", priority: "high", due_at: null, status: "needs_confirmation",
  explanation: "human_review_required", evidence_pins: [{ ref: { id: { value: "ev-17" } } }] };
const obligation = { id: 7, project_id: 3, contract_id: null, task_id: null, title: "Передать акт",
  status: "needs_confirmation", due_date: null, due_time: null, timezone: "Europe/Moscow", result_note: null,
  source_type: "evidence", source_name: "Договор.pdf", source_excerpt: "п. 5.2", confidence: 0.76,
  record_version: 3, evidence_pins: [{ ref: { id: { value: "ev-17" } } }], review_state: "needs_review",
  escalation_level: 1 };

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "X-Request-ID": "test-request" } });
}

function initialFetch() {
  return vi.fn()
    .mockResolvedValueOnce(response({ items: [attentionItem], total: 1, offset: 0, limit: 50,
      generated_at: "2026-09-05T10:00:00Z", external_actions_created: false }))
    .mockResolvedValueOnce(response({ obligations: [obligation], count: 1 }))
    .mockResolvedValueOnce(response({ notifications: [], unread: 0 }));
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("useManagementCenter", () => {
  it("loads the project-scoped snapshot through existing endpoints", async () => {
    const fetchMock = initialFetch();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(3));
    await waitFor(() => expect(result.current.state.loadState).toBe("ready"));
    expect(result.current.state.attention[0]).toMatchObject({ entityId: 7, recordVersion: 3 });
    expect(result.current.state.obligations[0].id).toBe(7);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/management/v2/attention?project_id=3",
      "/management/obligations?project_id=3",
      "/management/notifications?project_id=3",
    ]);
  });

  it("does not request data without a selected project", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(null));
    expect(result.current.state.loadState).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when the server shape is incomplete", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ items: [], total: 0, generated_at: "bad", external_actions_created: false }))
      .mockResolvedValueOnce(response({ obligations: [], count: 0 }))
      .mockResolvedValueOnce(response({ notifications: [], unread: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(3));
    await waitFor(() => expect(result.current.state.loadState).toBe("error"));
    expect(result.current.state.error).toContain("неожиданном формате");
  });

  it("loads exact obligation history", async () => {
    const fetchMock = initialFetch();
    fetchMock.mockResolvedValueOnce(response({ history: [{ sequence: 1, event: "created", from_status: null,
      to_status: "needs_confirmation", record_version: 1, reason: null, evidence_pins: [],
      occurred_at: "2026-09-05T10:00:00Z" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(3));
    await waitFor(() => expect(result.current.state.loadState).toBe("ready"));
    await act(async () => result.current.loadHistory("obligation", 7));
    expect(result.current.state.history[0]).toMatchObject({ sequence: 1, recordVersion: 1 });
    expect(String(fetchMock.mock.calls[3][0])).toBe("/management/v2/obligations/7/history");
  });

  it("surfaces a CAS conflict and does not silently retry a mutation", async () => {
    const fetchMock = initialFetch();
    fetchMock.mockResolvedValueOnce(response({ detail: "version_conflict" }, 409));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(3));
    await waitFor(() => expect(result.current.state.loadState).toBe("ready"));
    await act(async () => result.current.transitionObligation(result.current.state.obligations[0], "confirmed"));
    expect(result.current.state.mutationState).toBe("conflict");
    expect(result.current.state.mutationMessage).toContain("изменена другим пользователем");
    expect(fetchMock).toHaveBeenCalledTimes(4);
    const options = fetchMock.mock.calls[3][1] as RequestInit;
    expect(JSON.parse(String(options.body))).toMatchObject({ expected_version: 3, status: "confirmed" });
  });

  it("uses the published proposal and durable digest contracts", async () => {
    const fetchMock = initialFetch();
    const proposed = { kind: "task", entity_type: "obligation", entity_id: 9, record_version: 2,
      status: "needs_confirmation", review_state: "needs_review", task_id: null };
    fetchMock
      .mockResolvedValueOnce(response({ proposals: [proposed], external_actions_created: false }))
      .mockResolvedValueOnce(response({ proposal: { ...proposed, status: "confirmed", review_state: "confirmed",
        record_version: 4, task_id: 12 }, external_actions_created: false }))
      .mockResolvedValueOnce(response({ job_id: 41, status: "queued", external_actions_created: false }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useManagementCenter(3));
    await waitFor(() => expect(result.current.state.loadState).toBe("ready"));
    await act(async () => result.current.proposeMeetingActions(5, [{ kind: "task", title: "Подготовить акт",
      owner_user_id: 2, evidence_pins: [{ ref: { id: { value: "ev-17" } } }] }]));
    expect(result.current.state.proposals[0]).toMatchObject({ entityId: 9, recordVersion: 2 });
    await act(async () => result.current.confirmMeetingProposal(result.current.state.proposals[0], true));
    expect(result.current.state.proposals[0]).toMatchObject({ status: "confirmed", recordVersion: 4, taskId: 12 });
    await act(async () => result.current.enqueueDigest({ timezone: "Europe/Moscow", quietStart: "20:00",
      quietEnd: "08:00", channel: "in_app", localDate: "2026-09-05" }));
    expect(result.current.state.digestJob).toEqual({ jobId: 41, status: "queued", externalActionsCreated: false });
    expect(String(fetchMock.mock.calls[3][0])).toBe("/management/v2/meetings/5/proposals");
    expect(String(fetchMock.mock.calls[4][0])).toBe("/management/v2/proposals/obligation/9/confirm");
    expect(String(fetchMock.mock.calls[5][0])).toBe("/management/v2/digests");
  });
});
