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

  const rejoinableMailbox = {
    state: "stale", ready: false, valid: false, credential_generation: 2, cohort_record_version: 1,
    cutover: null, organization_id: 1, mail_connection_id: "mail-1",
    current_credential_generation: 3, current_binding_epoch: 1, actor_authority_version: 9,
  };

  it("offers to rejoin the mailbox for an admin when state is stale with all pointers present", () => {
    const onRejoinMailbox = vi.fn();
    render(<AutonomyReadinessPanel
      data={{ ...data, mailbox: rejoinableMailbox }}
      canRejoinMailbox onRejoinMailbox={onRejoinMailbox}
    />);
    fireEvent.click(screen.getByRole("button", { name: "Переподключить mailbox к текущему поколению" }));
    expect(onRejoinMailbox).toHaveBeenCalledTimes(1);
  });

  it("offers to rejoin the mailbox when state is ambiguous", () => {
    const onRejoinMailbox = vi.fn();
    render(<AutonomyReadinessPanel
      data={{ ...data, mailbox: { ...rejoinableMailbox, state: "ambiguous" } }}
      canRejoinMailbox onRejoinMailbox={onRejoinMailbox}
    />);
    expect(screen.getByRole("button", { name: "Переподключить mailbox к текущему поколению" })).toBeInTheDocument();
  });

  it.each(["missing", "producer_ready", "valid_not_pilot"])(
    "hides the rejoin-mailbox control for state %s",
    (state) => {
      const onRejoinMailbox = vi.fn();
      render(<AutonomyReadinessPanel
        data={{ ...data, mailbox: { ...rejoinableMailbox, state } }}
        canRejoinMailbox onRejoinMailbox={onRejoinMailbox}
      />);
      expect(screen.queryByRole("button", { name: /Переподключить mailbox/ })).not.toBeInTheDocument();
    },
  );

  it("hides the rejoin-mailbox control for a non-admin or without a handler", () => {
    const onRejoinMailbox = vi.fn();
    render(<AutonomyReadinessPanel
      data={{ ...data, mailbox: rejoinableMailbox }}
      canRejoinMailbox={false} onRejoinMailbox={onRejoinMailbox}
    />);
    expect(screen.queryByRole("button", { name: /Переподключить mailbox/ })).not.toBeInTheDocument();
    cleanup();
    render(<AutonomyReadinessPanel data={{ ...data, mailbox: rejoinableMailbox }} canRejoinMailbox />);
    expect(screen.queryByRole("button", { name: /Переподключить mailbox/ })).not.toBeInTheDocument();
  });

  it("hides the rejoin-mailbox control when a required pointer is missing", () => {
    const onRejoinMailbox = vi.fn();
    render(<AutonomyReadinessPanel
      data={{ ...data, mailbox: { ...rejoinableMailbox, actor_authority_version: null } }}
      canRejoinMailbox onRejoinMailbox={onRejoinMailbox}
    />);
    expect(screen.queryByRole("button", { name: /Переподключить mailbox/ })).not.toBeInTheDocument();
  });

  const NOW = new Date("2026-10-13T08:00:00Z");
  const mailboxWithAccess = (validUntil: string) => ({
    state: "producer_ready", ready: true, valid: true, credential_generation: 12, cohort_record_version: 2,
    cutover: null, organization_id: 1, mail_connection_id: "mail-1",
    current_credential_generation: 12, current_binding_epoch: 1, actor_authority_version: 4,
    authority_valid_until: validUntil,
  });

  it.each([
    ["2026-10-13T07:00:00Z", "expired", /Доступ системы к почте истёк/],
    ["2026-10-13T20:00:00Z", "expiring", /Доступ системы к почте истекает/],
    ["2026-10-19T08:00:00Z", "ok", /Доступ системы к почте действует до/],
  ])("shows mail access valid until %s as %s", (validUntil, state, text) => {
    const { container } = render(<AutonomyReadinessPanel
      data={{ ...data, mailbox: mailboxWithAccess(validUntil) }} now={NOW} />);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(container.querySelector(`[data-mail-access="${state}"]`)).not.toBeNull();
  });

  it("offers the owner a 7-day mail access renewal", () => {
    const onRenew = vi.fn();
    render(<AutonomyReadinessPanel data={{ ...data, mailbox: mailboxWithAccess("2026-10-13T07:00:00Z") }}
      now={NOW} canRenewMailboxAuthority onRenewMailboxAuthority={onRenew} />);
    fireEvent.click(screen.getByRole("button", { name: "Продлить доступ к почте на 7 дней" }));
    expect(onRenew).toHaveBeenCalledTimes(1);
  });

  it("hides the renewal for a non-admin, without a handler or without pointers", () => {
    const onRenew = vi.fn();
    render(<AutonomyReadinessPanel data={{ ...data, mailbox: mailboxWithAccess("2026-10-13T07:00:00Z") }}
      now={NOW} canRenewMailboxAuthority={false} onRenewMailboxAuthority={onRenew} />);
    expect(screen.queryByRole("button", { name: "Продлить доступ к почте на 7 дней" })).toBeNull();
    cleanup();
    render(<AutonomyReadinessPanel data={{ ...data, mailbox: { ...mailboxWithAccess("2026-10-13T07:00:00Z"), actor_authority_version: null } }}
      now={NOW} canRenewMailboxAuthority onRenewMailboxAuthority={onRenew} />);
    expect(screen.queryByRole("button", { name: "Продлить доступ к почте на 7 дней" })).toBeNull();
  });

  it("offers the 7-day AUTO pilot next to the CONFIRM launch once the mail source chain is ready", () => {
    const onEnableAutoPilot = vi.fn();
    render(<AutonomyReadinessPanel data={{ ...data, mailbox: { ...data.mailbox, auto_source_chain_ready: true } }}
      canBootstrapAuthority onBootstrapAuthority={vi.fn()}
      canLaunchPilot onLaunchPilot={vi.fn()} onEnableAutoPilot={onEnableAutoPilot} />);
    fireEvent.click(screen.getByRole("button", { name: "Включить AUTO для задач и уведомлений на 7 дней" }));
    expect(onEnableAutoPilot).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Внешние письма — всё равно только по подтверждению/)).toBeInTheDocument();
    expect(screen.queryByText(/этап V6-07/)).toBeNull();
  });

  it.each([
    ["false", false],
    ["absent (older server)", undefined],
  ])("hides the 7-day AUTO button and explains why when the chain flag is %s", (_label, ready) => {
    const onLaunchPilot = vi.fn();
    const onBootstrap = vi.fn();
    render(<AutonomyReadinessPanel
      data={{ ...data, overall: { status: "BLOCKED", blockers: ["mailbox_source_chain_not_ready"], warnings: [] },
        mailbox: { ...data.mailbox, auto_source_chain_ready: ready, auto_blocked_reason: "mailbox_cutover_primary_read_required" } }}
      canBootstrapAuthority onBootstrapAuthority={onBootstrap}
      canLaunchPilot onLaunchPilot={onLaunchPilot} onEnableAutoPilot={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Включить AUTO для задач и уведомлений на 7 дней" })).toBeNull();
    expect(screen.getByText("AUTO для почты появится после перехода почты на основное чтение (этап V6-07)")).toBeInTheDocument();
    expect(screen.getByText(/Почта ещё не на основном чтении/)).toBeInTheDocument();
    // Mandate restore and the CONFIRM launch stay available.
    fireEvent.click(screen.getByRole("button", { name: "Восстановить мандат пилота" }));
    fireEvent.click(screen.getByRole("button", { name: "Запустить пилот (всё через подтверждение)" }));
    expect(onBootstrap).toHaveBeenCalledTimes(1);
    expect(onLaunchPilot).toHaveBeenCalledTimes(1);
  });

  it("hides the AUTO pilot control without the launch control", () => {
    render(<AutonomyReadinessPanel data={data} canBootstrapAuthority onBootstrapAuthority={vi.fn()}
      onEnableAutoPilot={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Включить AUTO для задач и уведомлений на 7 дней" })).toBeNull();
  });

  it("has no controls capable of changing policy, authority or mailbox", () => {
    const { container } = render(<AutonomyReadinessPanel data={data} />);
    expect(container.querySelector("button, input, select, textarea, form")).toBeNull();
    expect(screen.getByText(/не изменяет policy, authority, mailbox/)).toBeInTheDocument();
  });
});
