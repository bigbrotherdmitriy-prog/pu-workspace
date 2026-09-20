import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MeetingsModule, type BookableResourceRow, type MeetingRow } from "./MeetingsModule";


const room: BookableResourceRow = {
  id: 71, record_version: 1, managing_project_id: 12,
  kind: "room", name: "Переговорная Север", timezone: "Europe/Moscow",
  capacity: 8, active: true,
};

function props(overrides: Record<string, unknown> = {}) {
  return {
    projectId: 12, collapsed: false, meetings: [] as MeetingRow[], title: "", date: "",
    duration: "60", agenda: "", participantUserIds: [], participantContactIds: [],
    members: [], contacts: [], resources: [room], resourceIds: [], canManageResources: true,
    resourceName: "", resourceKind: "room" as const, resourceTimezone: "Europe/Moscow",
    resourceCapacity: "", onTitleChange: vi.fn(), onDateChange: vi.fn(),
    onDurationChange: vi.fn(), onAgendaChange: vi.fn(), onParticipantUserIdsChange: vi.fn(),
    onParticipantContactIdsChange: vi.fn(), onResourceIdsChange: vi.fn(),
    onResourceNameChange: vi.fn(), onResourceKindChange: vi.fn(), onResourceTimezoneChange: vi.fn(),
    onResourceCapacityChange: vi.fn(), onCreateResource: vi.fn(), onDeactivateResource: vi.fn(),
    onCreate: vi.fn(), onRecordMinutes: vi.fn(), ...overrides,
  };
}

describe("MeetingsModule resources", () => {
  it("selects a structured resource and exposes catalog actions to a manager", () => {
    const values = props();
    render(<MeetingsModule {...values} />);

    const select = screen.getByLabelText("Помещения и ресурсы") as HTMLSelectElement;
    select.options[0].selected = true;
    fireEvent.change(select);
    expect(values.onResourceIdsChange).toHaveBeenCalledWith([71]);
    fireEvent.click(screen.getByRole("button", { name: "Отключить" }));
    expect(values.onDeactivateResource).toHaveBeenCalledWith(room);
  });

  it("shows shared resources and a non-blocking capacity warning", () => {
    const meeting: MeetingRow = {
      id: 8, title: "Переговоры", status: "planned", participant_user_ids: [],
      participant_contact_ids: [], participant_refs: [], resource_ids: [71], resource_refs: [room],
      has_conflicts: true, conflict_count: 1,
      conflicts: [{ meeting_id: 7, project_id: 12, title: "Другая встреча",
        overlap_from: "2026-09-26T10:30:00Z", overlap_to: "2026-09-26T11:00:00Z",
        participants: [], resources: [room], redacted: false }],
      has_resource_warnings: true,
      resource_warnings: [{ code: "capacity_exceeded", resource_id: 71,
        resource_name: room.name, capacity: 8, participant_count: 10 }],
    };
    render(<MeetingsModule {...props({ meetings: [meeting] })} />);

    expect(screen.getByText((_, element) =>
      element?.tagName === "P" && element.textContent === "Ресурсы: Переговорная Север",
    )).toBeInTheDocument();
    expect(screen.getByText(/ресурсы: Переговорная Север/)).toBeInTheDocument();
    expect(screen.getByText("Проверьте вместимость")).toBeInTheDocument();
    expect(screen.getByText(/участников 10, вместимость 8/)).toBeInTheDocument();
  });
});
