import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MeetingsModule, type MeetingRow } from "./MeetingsModule";
vi.mock("./MeetingSourcePanel", () => ({ MeetingSourcePanel: () => <div>Exact source panel</div> }));
afterEach(cleanup);
function setup(meeting: MeetingRow) {
  const onRecordMinutes = vi.fn();
  render(<MeetingsModule collapsed={false} projectId={2} meetings={[meeting]} title="" date="" agenda=""
    onTitleChange={vi.fn()} onDateChange={vi.fn()} onAgendaChange={vi.fn()} onCreate={vi.fn()}
    onRecordMinutes={onRecordMinutes} />);
  return onRecordMinutes;
}
it("allows explicit correction of completed minutes without auto-analysis", () => {
  const meeting = { id: 5, title: "Synthetic meeting", status: "completed", minutes: "Synthetic minutes", record_version: 3 };
  const record = setup(meeting);
  expect(record).not.toHaveBeenCalled();
  expect(screen.getByText("Exact source panel")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Внести протокол и проанализировать" }));
  expect(record).toHaveBeenCalledExactlyOnceWith(meeting);
});
it("never offers editing or source binding for cancelled meetings", () => {
  const record = setup({ id: 5, title: "Synthetic cancelled", status: "cancelled", minutes: "Synthetic minutes", record_version: 3 });
  expect(screen.queryByRole("button", { name: "Внести протокол и проанализировать" })).not.toBeInTheDocument();
  expect(screen.queryByText("Exact source panel")).not.toBeInTheDocument();
  expect(record).not.toHaveBeenCalled();
});
it("does not expose source confirmation without an exact meeting revision", () => {
  setup({ id: 5, title: "Synthetic legacy", status: "completed", minutes: "Synthetic minutes" });
  expect(screen.queryByText("Exact source panel")).not.toBeInTheDocument();
});
