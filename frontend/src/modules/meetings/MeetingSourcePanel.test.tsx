import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import { MeetingSourcePanel } from "./MeetingSourcePanel";
import { parseEligibleSources, parseMeetingBinding } from "./meetingSourceModel";
import { parseMeetingProposalEnvelope } from "../management/managementReadModel";
vi.mock("../../api/client", async original => ({ ...await original<typeof import("../../api/client")>(), api: vi.fn() }));
const mockApi = vi.mocked(api);
const sourceId = "10000000-0000-4000-8000-000000000001", versionId = "10000000-0000-4000-8000-000000000002";
const bindingId = "10000000-0000-4000-8000-000000000003";
const pin = { ref: { namespace: "pu", type: "evidence", tenant_id: { kind: "int", value: "1" },
  id: { kind: "uuid", value: "10000000-0000-4000-8000-000000000004" } }, version_kind: "revision", value: 1 };
const eligible = { meeting_id: 5, meeting_record_version: 2, external_actions_created: false,
  sources: [{ source_id: sourceId, source_version_id: versionId, evidence_pins: [pin] }] };
const binding = { meeting_id: 5, meeting_record_version: 3, binding_id: bindingId,
  source_id: sourceId, source_version_id: versionId, origin_status: "bound", confirmation_available: true, external_actions_created: false };
const proposal = { kind: "task", entity_type: "obligation", entity_id: 9, record_version: 1,
  status: "needs_confirmation", review_state: "needs_review", task_id: null, ...binding };
const props = { projectId: 2, meetingId: 5, recordVersion: 2, members: [{ user_id: 3, name: "Synthetic member" }] };
const unbound = { meeting_id: 5, meeting_record_version: 2, origin_status: "invalid_source",
  origin_reason: "meeting_source_binding_required", confirmation_available: false, external_actions_created: false, proposals: [] };
beforeEach(() => { mockApi.mockReset(); });
afterEach(cleanup);

it("preserves the server's exact evidence revision without inventing revision one", () => {
  const result = parseEligibleSources({ ...eligible, sources: [{ ...eligible.sources[0], evidence_pins: [{ ...pin, value: 7 }] }] }, 5, 2);
  expect(result[0].evidencePins[0].value).toBe(7);
  expect(() => parseEligibleSources({ ...eligible, sources: [{ ...eligible.sources[0], evidence_pins: [pin, pin] }] }, 5, 2)).toThrow();
});

it("keeps user draft on retention denial and never retries a mutation", async () => {
  mockApi.mockImplementation(async path => {
    if (path.includes("eligible-sources")) return { ...eligible, meeting_record_version: 3 };
    if (path.includes("/proposals?")) return { ...binding, proposals: [] };
    throw new ApiError("private retention detail", 422, "safe-test");
  });
  render(<MeetingSourcePanel {...props} recordVersion={3} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  fireEvent.change(await screen.findByLabelText("Название действия"), { target: { value: "Keep this draft" } });
  fireEvent.change(screen.getByLabelText("Ответственный"), { target: { value: "3" } });
  fireEvent.click(screen.getByLabelText("Доказательство 1"));
  fireEvent.click(screen.getByText("Подготовить предложение"));
  expect(await screen.findByText(/Источник или действие недоступны/)).toBeInTheDocument();
  expect(screen.queryByText(/private retention detail/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  expect(await screen.findByLabelText("Название действия")).toHaveValue("Keep this draft");
  expect(mockApi.mock.calls.filter(([, o]) => o?.method === "POST")).toHaveLength(1);
});

it("rejects another meeting's unbound history instead of treating it as local authority", async () => {
  mockApi.mockImplementation(async path => path.includes("eligible-sources") ? eligible : { ...unbound, meeting_id: 99 });
  render(<MeetingSourcePanel {...props} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  expect(await screen.findByText(/Источник или действие недоступны/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Подтверждённый источник")).not.toBeInTheDocument();
});

it("drops prior binding when the project changes without relying on parent remount", async () => {
  mockApi.mockImplementation(async path => path.includes("eligible-sources") ? { ...eligible, meeting_record_version: 3 } : { ...binding, proposals: [] });
  const view = render(<MeetingSourcePanel {...props} recordVersion={3} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  fireEvent.change(await screen.findByLabelText("Название действия"), { target: { value: "Old project draft" } });
  view.rerender(<MeetingSourcePanel {...props} projectId={7} recordVersion={3} />);
  expect(screen.queryByLabelText("Название действия")).not.toBeInTheDocument();
  expect(mockApi.mock.calls.every(([, o]) => !o?.method)).toBe(true);
});

it("requires a complete exact bound origin and rejects conflicting envelope pins", () => {
  expect(parseMeetingBinding(binding).bindingId).toBe(bindingId);
  expect(() => parseMeetingBinding({ origin_status: "bound", confirmation_available: true })).toThrow();
  expect(parseMeetingProposalEnvelope({ ...binding, proposals: [proposal] })[0].binding?.sourceId).toBe(sourceId);
  expect(() => parseMeetingProposalEnvelope({ ...binding, source_id: versionId, proposals: [proposal] })).toThrow();
});

it("keeps historical denied rows separate from the current binding", () => {
  const historical = { kind: "task", entity_type: "obligation", entity_id: 10, record_version: 1,
    status: "needs_confirmation", review_state: "needs_review", task_id: null,
    origin_status: "invalid_source", origin_reason: "meeting_source_binding_required", confirmation_available: false };
  const rows = parseMeetingProposalEnvelope({ ...binding, proposals: [historical, proposal] });
  expect(rows[0].binding).toBeUndefined();
  expect(rows[0].originStatus).toBe("invalid_source");
  expect(rows[0].confirmationAvailable).toBe(false);
  expect(rows[1].binding?.bindingId).toBe(bindingId);
});
it.each([
  { ...eligible, meeting_id: 6 }, { ...eligible, meeting_record_version: 3 },
  { ...eligible, external_actions_created: true },
  { ...eligible, sources: [...eligible.sources, ...eligible.sources] },
  { ...eligible, sources: [{ ...eligible.sources[0], evidence_pins: [{ ...pin, value: 0 }] }] },
  { ...eligible, sources: [{ ...eligible.sources[0], evidence_pins: [{ ...pin, private_payload: "synthetic" }] }] },
])("rejects stale, malformed or ambiguous source responses", value => {
  expect(() => parseEligibleSources(value, 5, 2)).toThrow();
});

it("does not mutate on discovery; human binding, proposal and confirmation are separate", async () => {
  mockApi.mockImplementation(async (path, options) => {
    if (path.includes("eligible-sources")) return eligible;
    if (path.includes("/proposals?")) return unbound;
    if (path.endsWith("source-binding")) return binding;
    if (path.endsWith("/proposals")) return { ...binding, proposals: [proposal] };
    if (path.endsWith("/confirm")) return { external_actions_created: false,
      proposal: { ...proposal, record_version: 3, status: "confirmed", review_state: "verified", task_id: 12 } };
    throw new Error(`unexpected ${options?.method}`);
  });
  render(<MeetingSourcePanel {...props} />);
  expect(mockApi).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  fireEvent.change(await screen.findByLabelText("Подтверждённый источник"), { target: { value: `${sourceId}:${versionId}` } });
  expect(mockApi.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  fireEvent.click(screen.getByText("Привязать выбранный источник"));
  fireEvent.change(await screen.findByLabelText("Название действия"), { target: { value: "Synthetic internal task" } });
  fireEvent.change(screen.getByLabelText("Ответственный"), { target: { value: "3" } });
  fireEvent.click(screen.getByLabelText("Доказательство 1"));
  fireEvent.click(screen.getByText("Подготовить предложение"));
  fireEvent.click(await screen.findByText("Подтвердить"));
  expect(await screen.findByText(/Предложение подтверждено/)).toBeInTheDocument();
  const bodies = mockApi.mock.calls.filter(([, o]) => o?.method === "POST").map(([, o]) => JSON.parse(String(o?.body)));
  expect(bodies).toHaveLength(3);
  expect(bodies[0]).toMatchObject({ expected_version: 2, project_id: 2, source_id: sourceId, source_version_id: versionId });
  expect(bodies[1]).toMatchObject({ meeting_source_binding_id: bindingId,
    candidates: [{ owner_user_id: 3, evidence_pins: [pin] }] });
  expect(bodies[2]).toEqual({ project_id: 2, expected_version: 1, create_internal_task: true });
});

it("clears source and confirmation state on a conflict without automatic retry", async () => {
  mockApi.mockImplementation(async path => {
    if (path.includes("eligible-sources")) return eligible;
    if (path.includes("/proposals?")) return unbound;
    throw new ApiError("private raw", 409, "safe-test");
  });
  render(<MeetingSourcePanel {...props} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  fireEvent.change(await screen.findByLabelText("Подтверждённый источник"), { target: { value: `${sourceId}:${versionId}` } });
  fireEvent.click(screen.getByText("Привязать выбранный источник"));
  expect(await screen.findByText(/Протокол или источник изменился/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Название действия")).not.toBeInTheDocument();
  expect(screen.queryByText(/private raw/)).not.toBeInTheDocument();
  expect(mockApi).toHaveBeenCalledTimes(3);
});

it("ignores a late source list after changing project or meeting version", async () => {
  let resolve!: (value: unknown) => void;
  mockApi.mockImplementation(async path => path.includes("eligible-sources") ? new Promise(r => { resolve = r; }) : unbound);
  const view = render(<MeetingSourcePanel key="old" {...props} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  view.rerender(<MeetingSourcePanel key="new" {...props} projectId={7} />);
  resolve(eligible);
  await waitFor(() => expect(screen.queryByLabelText("Подтверждённый источник")).not.toBeInTheDocument());
  expect(mockApi).toHaveBeenCalledTimes(2);
});

it("restores an existing binding and proposals without rebind or another POST", async () => {
  mockApi.mockImplementation(async path => path.includes("eligible-sources")
    ? { ...eligible, meeting_record_version: 3 } : { ...binding, proposals: [proposal] });
  render(<MeetingSourcePanel {...props} recordVersion={3} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  expect(await screen.findByText(/Существующая привязка и предложения загружены/)).toBeInTheDocument();
  expect(screen.getByText("Подтвердить")).toBeEnabled();
  expect(screen.getByText("Привязать выбранный источник")).toBeDisabled();
  expect(mockApi.mock.calls.every(([, o]) => !o?.method)).toBe(true);
});

it("keeps zero-evidence sources disabled without rejecting valid neighbours", () => {
  const result = parseEligibleSources({ ...eligible, sources: [eligible.sources[0],
    { source_id: bindingId, source_version_id: versionId, evidence_pins: [] }] }, 5, 2);
  expect(result).toHaveLength(2);
  expect(result[1].evidencePins).toHaveLength(0);
});

it.each([
  { record_version: 3, status: "needs_confirmation", review_state: "needs_review", task_id: null },
  { record_version: 3, status: "confirmed", review_state: "verified", task_id: null },
  { record_version: 1, status: "confirmed", review_state: "verified", task_id: 12 },
  { record_version: 3, status: "confirmed", review_state: "verified", task_id: 12, source_id: versionId },
])("never reports success for an incomplete or mismatched HTTP 200 confirmation", async change => {
  mockApi.mockImplementation(async path => {
    if (path.includes("eligible-sources")) return { ...eligible, meeting_record_version: 3 };
    if (path.includes("/proposals?")) return { ...binding, proposals: [proposal] };
    return { external_actions_created: false, proposal: { ...proposal, ...change } };
  });
  render(<MeetingSourcePanel {...props} recordVersion={3} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  fireEvent.click(await screen.findByText("Подтвердить"));
  expect(await screen.findByText(/Источник или действие недоступны/)).toBeInTheDocument();
  expect(screen.queryByText(/Предложение подтверждено/)).not.toBeInTheDocument();
  expect(mockApi.mock.calls.filter(([, o]) => o?.method === "POST")).toHaveLength(1);
});

it("requires explicit evidence selection and caps it at twenty without truncation", async () => {
  const pins = Array.from({ length: 21 }, (_, i) => ({ ...pin, ref: { ...pin.ref,
    id: { kind: "uuid", value: `10000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}` } } }));
  mockApi.mockImplementation(async path => path.includes("eligible-sources")
    ? { ...eligible, meeting_record_version: 3, sources: [{ ...eligible.sources[0], evidence_pins: pins }] }
    : { ...binding, proposals: [] });
  render(<MeetingSourcePanel {...props} recordVersion={3} />);
  fireEvent.click(screen.getByText("Выбрать источник протокола"));
  await screen.findByLabelText("Доказательство 1");
  expect(screen.getByText("Подготовить предложение")).toBeDisabled();
  for (let i = 1; i <= 20; i++) fireEvent.click(screen.getByLabelText(`Доказательство ${i}`));
  expect(screen.getByLabelText("Доказательство 21")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Доказательство 1"));
  expect(screen.getByLabelText("Доказательство 21")).toBeEnabled();
  expect(mockApi.mock.calls.every(([, o]) => !o?.method)).toBe(true);
});
