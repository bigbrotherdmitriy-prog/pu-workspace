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
});
