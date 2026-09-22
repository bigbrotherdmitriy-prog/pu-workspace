import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SyncStatusPanel } from "./SyncStatusPanel";

afterEach(cleanup);

describe("mobile synchronization status", () => {
  it("shows the unsynchronized count and offers a manual retry", () => {
    const onSync = vi.fn();
    render(<SyncStatusPanel
      online
      summary={{ queued: 4, syncing: 0, attention: 0, authRequired: 0, conflicts: 0, total: 4 }}
      conflicts={[]}
      onSync={onSync}
      onResolve={vi.fn()}
    />);
    expect(screen.getByText("Ожидает синхронизации: 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Синхронизировать сейчас/ }));
    expect(onSync).toHaveBeenCalledOnce();
  });

  it("never hides a true conflict and requires an explicit resolution", () => {
    const onResolve = vi.fn();
    render(<SyncStatusPanel
      online
      summary={{ queued: 0, syncing: 0, attention: 0, authRequired: 0, conflicts: 1, total: 1 }}
      conflicts={[{
        id: 8, command_id: 4, entity_type: "task", entity_id: 33,
        base_values: { status: "assigned" }, local_values: { status: "cancelled" },
        server_values: { status: "in_progress" }, conflicting_fields: ["status"],
        server_record_version: 2, status: "unresolved",
      }]}
      onSync={vi.fn()}
      onResolve={onResolve}
    />);
    expect(screen.getByText(/Ни одна версия не была затёрта/)).toBeInTheDocument();
    expect(screen.getByText("Офлайн: cancelled")).toBeInTheDocument();
    expect(screen.getByText("Сервер: in_progress")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Выбрать последнюю по времени" }));
    expect(onResolve).toHaveBeenCalledWith(8, "latest_write_wins");
  });
});
