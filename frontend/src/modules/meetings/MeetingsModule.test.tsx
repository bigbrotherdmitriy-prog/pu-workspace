import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MeetingsModule,
  type BookableResourceRow,
  type MeetingAuthorityState,
  type MeetingProposal,
  type MeetingRow,
  type MeetingSourceCandidate,
} from "./MeetingsModule";

afterEach(cleanup);

const room: BookableResourceRow = {
  id: 71, record_version: 1, managing_project_id: 12,
  kind: "room", name: "Переговорная Север", timezone: "Europe/Moscow",
  capacity: 8, active: true,
};

const meeting = (overrides: Partial<MeetingRow> = {}): MeetingRow => ({
  id: 7,
  record_version: 2,
  title: "Производственное совещание",
  participant_user_ids: [],
  participant_contact_ids: [],
  participant_refs: [],
  resource_ids: [],
  resource_refs: [],
  has_conflicts: false,
  conflict_count: 0,
  conflicts: [],
  has_resource_warnings: false,
  resource_warnings: [],
  minutes: "Подготовить акт до 25.09.2026",
  status: "completed",
  can_edit: true,
  can_manage: true,
  ...overrides,
});

const authority = (overrides: Partial<MeetingAuthorityState> = {}): MeetingAuthorityState => ({
  loaded: true,
  loading: false,
  candidates: [{
    document_id: 31,
    display_name: "Протокол.pdf",
    media_type: "application/pdf",
    source_id: "source-1",
    source_version_id: "version-1",
    evidence_id: "evidence-1",
    materialization_id: "materialization-1",
  }],
  proposals: [],
  ...overrides,
});

function renderModule(
  row: MeetingRow,
  state: MeetingAuthorityState,
  callbacks: {
    onBindSource?: ReturnType<typeof vi.fn<(meeting: MeetingRow, source: MeetingSourceCandidate) => void>>;
    onConfirmProposal?: ReturnType<typeof vi.fn<(meeting: MeetingRow, proposal: MeetingProposal) => void>>;
    onRecordMinutes?: ReturnType<typeof vi.fn<(meeting: MeetingRow) => void>>;
  } = {},
) {
  const onBindSource = callbacks.onBindSource || vi.fn<(meeting: MeetingRow, source: MeetingSourceCandidate) => void>();
  const onConfirmProposal = callbacks.onConfirmProposal || vi.fn<(meeting: MeetingRow, proposal: MeetingProposal) => void>();
  const onRecordMinutes = callbacks.onRecordMinutes || vi.fn<(meeting: MeetingRow) => void>();
  render(<MeetingsModule
    projectId={12}
    collapsed={false}
    meetings={[row]}
    title=""
    date=""
    duration="60"
    agenda=""
    participantUserIds={[]}
    participantContactIds={[]}
    members={[]}
    contacts={[]}
    resources={[room]}
    resourceIds={[]}
    canManageResources={true}
    resourceName=""
    resourceKind="room"
    resourceTimezone="Europe/Moscow"
    resourceCapacity=""
    onTitleChange={vi.fn()}
    onDateChange={vi.fn()}
    onDurationChange={vi.fn()}
    onAgendaChange={vi.fn()}
    onParticipantUserIdsChange={vi.fn()}
    onParticipantContactIdsChange={vi.fn()}
    onResourceIdsChange={vi.fn()}
    onResourceNameChange={vi.fn()}
    onResourceKindChange={vi.fn()}
    onResourceTimezoneChange={vi.fn()}
    onResourceCapacityChange={vi.fn()}
    onCreateResource={vi.fn()}
    onDeactivateResource={vi.fn()}
    onCreate={vi.fn()}
    onRecordMinutes={onRecordMinutes}
    authority={{ [row.id]: state }}
    busyAuthorityId={0}
    onRefreshAuthority={vi.fn()}
    onBindSource={onBindSource}
    onConfirmProposal={onConfirmProposal}
  />);
  return { onBindSource, onConfirmProposal, onRecordMinutes };
}

describe("MeetingsModule meeting authority", () => {
  it("lets a manager bind one exact current local-upload version", () => {
    const row = meeting();
    const state = authority();
    const { onBindSource } = renderModule(row, state);
    expect(screen.getByLabelText(`Источник протокола ${row.title}`)).toHaveValue("materialization-1");
    fireEvent.click(screen.getByRole("button", { name: "Привязать точную версию и подготовить предложения" }));
    expect(onBindSource).toHaveBeenCalledWith(row, state.candidates[0]);
    expect(screen.getByText(/Ничего не создаётся без точного источника/)).toBeInTheDocument();
  });

  it("shows evidence and confirms only one selected proposal", () => {
    const row = meeting({ record_version: 3 });
    const proposal = {
      id: 41,
      record_version: 1,
      proposal_type: "task" as const,
      payload: {
        title: "Подготовить акт",
        excerpt: "Подготовить акт до 25.09.2026",
        due_date_evidence_quote: "до 25.09.2026",
      },
      status: "proposed" as const,
    };
    const { onConfirmProposal } = renderModule(row, authority({
      candidates: [],
      proposals: [proposal],
      source_binding: {
        display_name: "Протокол.pdf",
        source_id: "source-1",
        source_version_id: "version-1",
        evidence_id: "evidence-1",
        materialization_id: "materialization-1",
      },
    }));
    expect(screen.getByText("Точная версия источника подтверждена")).toBeInTheDocument();
    expect(screen.getByText(/Подготовить акт до 25\.09\.2026/, { selector: "blockquote" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить только это действие" }));
    expect(onConfirmProposal).toHaveBeenCalledWith(row, proposal);
  });

  it("keeps viewer actions blocked while preserving proposal evidence", () => {
    const row = meeting({ can_edit: false, can_manage: false });
    renderModule(row, authority({
      candidates: [],
      proposals: [{
        id: 42,
        record_version: 1,
        proposal_type: "risk",
        payload: { title: "Риск задержки", excerpt: "Возможна задержка поставки" },
        status: "proposed",
      }],
    }));
    expect(screen.getByText("Возможна задержка поставки")).toBeInTheDocument();
    expect(screen.getByText("Ожидает подтверждения manager.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить только это действие" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: `Источник протокола ${row.title}` })).not.toBeInTheDocument();
  });

  it("shows a truthful stale/conflict error instead of implying success", () => {
    renderModule(meeting(), authority({ error: "Протокол изменился. Обновите карточку и выберите источник заново." }));
    expect(screen.getByRole("alert")).toHaveTextContent("Протокол изменился");
  });
});

describe("MeetingsModule resources", () => {
  it("shows shared resources and a non-blocking capacity warning", () => {
    const row = meeting({
      status: "planned", resource_ids: [room.id], resource_refs: [room],
      has_resource_warnings: true,
      resource_warnings: [{ code: "capacity_exceeded", resource_id: room.id,
        resource_name: room.name, capacity: 8, participant_count: 10 }],
    });
    renderModule(row, authority());
    expect(screen.getByText((_, element) =>
      element?.tagName === "P" && element.textContent === "Ресурсы: Переговорная Север",
    )).toBeInTheDocument();
    expect(screen.getByText("Проверьте вместимость")).toBeInTheDocument();
    expect(screen.getByText(/участников 10, вместимость 8/)).toBeInTheDocument();
  });
});
