import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AutonomyReadinessPanel, type AutonomyReadiness } from "./AutonomyReadinessPanel";

const data: AutonomyReadiness = {
  project_id: 17,
  observed_at: "2026-09-23T09:00:00Z",
  overall: { status: "DEGRADED", blockers: [], warnings: ["runtime_component_alignment_unverified"] },
  runtime: { state: "matching", pilot_enabled: true, producer_enabled: true, notification_enabled: true, scope_matches: true, component_alignment: "unverified" },
  policy: { revision: 4, hash_prefix: "0123456789ab", enabled: true, task_mode: "AUTO", notification_mode: "AUTO", external_message_mode: "CONFIRM", authority_epoch: 8, valid_until: "2026-09-24T09:00:00Z", ttl_seconds: 86400, ready: true, history: [{ revision: 4, enabled: true }, { revision: 3, enabled: false }] },
  authority: { state: "active", membership_role: "owner", authority_epoch: 8, record_version: 8, valid_until: "2026-09-24T09:00:00Z", ttl_seconds: 86400, ready: true },
  mailbox: { state: "ready", ready: true, credential_generation: 3, cohort_record_version: 2, cutover: { pilot_write: true, primary_read: false, actions: false } },
  quotas: { task_hourly: { used: 2, limit: 3 }, notification_project_hourly: { used: 3, limit: 3 }, notification_recipient_daily: { max_used: 4, limit: 10 } },
  operations: { actions: { SUCCEEDED: 5 }, receipts: { APPLIED: 5 }, jobs: { completed: 5 }, incidents: [] },
};

afterEach(cleanup);

describe("AutonomyReadinessPanel", () => {
  it("shows truthful degraded state, TTL, quota boundary and immutable CONFIRM controls", () => {
    render(<AutonomyReadinessPanel data={data} />);
    expect(screen.getByRole("region", { name: "Готовность AUTO" })).toBeInTheDocument();
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
    expect(screen.getByText("r4 · 0123456789ab")).toBeInTheDocument();
    expect(screen.getByText("r4 ON · r3 OFF")).toBeInTheDocument();
    expect(screen.getByText("AUTO / AUTO")).toBeInTheDocument();
    expect(screen.getByText("CONFIRM")).toBeInTheDocument();
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    expect(screen.getByText(/нельзя подтвердить/)).toBeInTheDocument();
  });

  it("offers to bootstrap authority only for an admin with the handler wired", () => {
    const onBootstrapAuthority = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority onBootstrapAuthority={onBootstrapAuthority} />);
    // The panel itself never gates on confirm -- that's the caller's job (App.tsx);
    // here it just proves the handler is wired and reachable.
    fireEvent.click(screen.getByRole("button", { name: "Восстановить мандат пилота" }));
    expect(onBootstrapAuthority).toHaveBeenCalledTimes(1);
  });

  it("offers to launch the pilot only alongside the bootstrap control, for an admin with the handler wired", () => {
    const onBootstrapAuthority = vi.fn();
    const onLaunchPilot = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority onBootstrapAuthority={onBootstrapAuthority}
      canLaunchPilot onLaunchPilot={onLaunchPilot} />);
    fireEvent.click(screen.getByRole("button", { name: "Запустить пилот (всё через подтверждение)" }));
    expect(onLaunchPilot).toHaveBeenCalledTimes(1);
  });

  it("offers to expand authority alongside the other controls, for an admin with the handler wired", () => {
    const onBootstrapAuthority = vi.fn();
    const onExpandAuthority = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority onBootstrapAuthority={onBootstrapAuthority}
      canExpandAuthority onExpandAuthority={onExpandAuthority} />);
    fireEvent.click(screen.getByRole("button", { name: "Выдать полный набор полномочий" }));
    expect(onExpandAuthority).toHaveBeenCalledTimes(1);
  });

  it("hides the expand-authority control without the bootstrap control shown first", () => {
    const onExpandAuthority = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority={false} canExpandAuthority onExpandAuthority={onExpandAuthority} />);
    expect(screen.queryByRole("button", { name: /Выдать полный набор полномочий/ })).not.toBeInTheDocument();
  });

  it("hides the launch-pilot control without the bootstrap control shown first", () => {
    const onLaunchPilot = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority={false} canLaunchPilot onLaunchPilot={onLaunchPilot} />);
    expect(screen.queryByRole("button", { name: /Запустить пилот/ })).not.toBeInTheDocument();
  });

  it("hides the bootstrap control for a non-admin or without a handler", () => {
    const onBootstrapAuthority = vi.fn();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority={false} onBootstrapAuthority={onBootstrapAuthority} />);
    expect(screen.queryByRole("button", { name: "Восстановить мандат пилота" })).not.toBeInTheDocument();
    cleanup();
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority />);
    expect(screen.queryByRole("button", { name: "Восстановить мандат пилота" })).not.toBeInTheDocument();
  });

  it("has no controls capable of changing policy, authority or mailbox", () => {
    const { container } = render(<AutonomyReadinessPanel data={data} />);
    expect(container.querySelector("button, input, select, textarea, form")).toBeNull();
    expect(screen.getByText(/не изменяет policy, authority, mailbox/)).toBeInTheDocument();
  });
});
