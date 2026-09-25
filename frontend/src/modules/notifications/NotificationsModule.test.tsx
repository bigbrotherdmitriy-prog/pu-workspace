import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NotificationsModule, type NotificationPolicy } from "./NotificationsModule";

const policy: NotificationPolicy = {
  record_version: 2,
  timezone: "Europe/Moscow",
  deadline_local_time: "09:00:00",
  quiet_start: "22:00:00",
  quiet_end: "07:00:00",
  escalation_delays: [0, 60],
  channels: ["in_app"],
  enabled: true,
  digest_enabled: false,
  digest_cadence: "daily",
  digest_local_time: "09:00:00",
};

describe("NotificationsModule digest settings", () => {
  it("shows safe digest settings and emits an explicit opt-in", () => {
    const onPolicyChange = vi.fn();
    const view = render(<NotificationsModule
      collapsed={false}
      notifications={[]}
      digests={[]}
      onRefresh={vi.fn()}
      onMarkRead={vi.fn()}
      policy={policy}
      canManagePolicy
      onPolicyChange={onPolicyChange}
      onSavePolicy={vi.fn()}
    />);
    expect(screen.getByText(/только подтверждённые активные элементы/i)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Включить сводку"));
    expect(onPolicyChange).toHaveBeenCalledWith(expect.objectContaining({ digest_enabled: true }));
    view.unmount();
  });

  it("keeps viewer controls read-only", () => {
    render(<NotificationsModule
      collapsed={false}
      notifications={[]}
      digests={[]}
      onRefresh={vi.fn()}
      onMarkRead={vi.fn()}
      policy={{ ...policy, digest_enabled: true }}
      canManagePolicy={false}
      onPolicyChange={vi.fn()}
      onSavePolicy={vi.fn()}
    />);
    expect(screen.getByLabelText("Включить сводку")).toBeDisabled();
    expect(screen.queryByText("Сохранить настройки сводки")).not.toBeInTheDocument();
  });

  it("renders immutable digest references without raw source content", () => {
    render(<NotificationsModule
      collapsed={false}
      notifications={[]}
      digests={[{
        id: 7, local_date: "2026-09-20", item_count: 1,
        item_refs: [{ entity_type: "risk", entity_id: 41, record_version: 2,
          status: "confirmed", proposal_origin: { evidence_id: "evidence-1", meeting_id: 9 } }],
        requested_channels: ["in_app"], created_at: "2026-09-20T09:00:00Z",
      }]}
      onRefresh={vi.fn()}
      onMarkRead={vi.fn()}
      policy={policy}
      canManagePolicy
      onPolicyChange={vi.fn()}
      onSavePolicy={vi.fn()}
    />);
    expect(screen.getByText("risk #41 · confirmed · основание закреплено")).toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE BODY/)).not.toBeInTheDocument();
  });

  it("filters the register to unread notifications", () => {
    const notification = (id: number, is_read: boolean) => ({
      id, record_version: 1, kind: "deadline", title: `Уведомление ${id}`,
      body: "Требует внимания", entity_type: "task", entity_id: id,
      is_read, created_at: "2026-09-25T09:00:00Z",
    });
    render(<NotificationsModule
      collapsed={false}
      notifications={[notification(1, false), notification(2, true)]}
      unreadOnly
      digests={[]}
      onRefresh={vi.fn()}
      onMarkRead={vi.fn()}
      policy={policy}
      canManagePolicy
      onPolicyChange={vi.fn()}
      onSavePolicy={vi.fn()}
    />);
    expect(screen.getByText("Уведомление 1")).toBeInTheDocument();
    expect(screen.queryByText("Уведомление 2")).not.toBeInTheDocument();
  });
});
