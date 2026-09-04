import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectSearchResults, type ProjectSearchHit } from "./ProjectSearchResults";

const duplicateMessages: ProjectSearchHit[] = [
  { id: 1, kind: "message", title: "Письмо по проекту", detail: "sender@example.test" },
  { id: 2, kind: "message", title: " ПИСЬМО ПО ПРОЕКТУ ", detail: " sender@example.test " },
];

afterEach(cleanup);

describe("ProjectSearchResults", () => {
  it("shows visually identical search records once", () => {
    render(<ProjectSearchResults query="письмо" hits={duplicateMessages} onOpen={vi.fn()} />);

    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("opens the original hit represented by a unique result", () => {
    const onOpen = vi.fn();
    render(<ProjectSearchResults query="письмо" hits={duplicateMessages} onOpen={onOpen} />);

    fireEvent.click(screen.getByRole("option"));
    expect(onOpen).toHaveBeenCalledWith(duplicateMessages[0]);
  });
});
