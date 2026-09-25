import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AndroidBottomNav } from "./AndroidBottomNav";

describe("AndroidBottomNav", () => {
  it("provides the production tabs and opens the complete menu", () => {
    const onNavigate = vi.fn();
    const onMore = vi.fn();
    const onUpload = vi.fn();
    render(<AndroidBottomNav active="ГПР и ДДС" activeFinanceTab="gpr" onNavigate={onNavigate} onMore={onMore} onUpload={onUpload} />);
    expect(screen.getByRole("button", { name: "ГПР" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "ДДС" }));
    expect(onNavigate).toHaveBeenCalledWith("ГПР и ДДС", "dds");
    fireEvent.click(screen.getByRole("button", { name: "Открыть всё меню" }));
    expect(onMore).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Добавить документ" }));
    expect(onUpload).toHaveBeenCalledOnce();
  });
});
