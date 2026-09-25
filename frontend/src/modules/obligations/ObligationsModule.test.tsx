import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ObligationsModule, type ObligationRow } from "./ObligationsModule";

const obligation = (id: number, due_date: string): ObligationRow => ({
  id, title: `Обязательство ${id}`, status: "in_progress", due_date,
  source_type: "document", source_name: "Договор.pdf", source_excerpt: "Основание", confidence: .9,
});

describe("ObligationsModule", () => {
  it("filters the register to overdue open obligations", () => {
    render(<ObligationsModule
      collapsed={false}
      filter="overdue"
      obligations={[obligation(1, "2020-01-01"), obligation(2, "2099-01-01")]}
      onUpdate={vi.fn()}
    />);
    expect(screen.getByText("Обязательство 1")).toBeInTheDocument();
    expect(screen.queryByText("Обязательство 2")).not.toBeInTheDocument();
  });
});
