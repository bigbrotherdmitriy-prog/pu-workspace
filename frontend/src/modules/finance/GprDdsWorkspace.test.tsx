import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GprDdsWorkspace } from "./GprDdsWorkspace";

afterEach(cleanup);

describe("GprDdsWorkspace", () => {
  it("keeps both primary workspaces mounted and switches the visible tab", () => {
    const onTabChange = vi.fn();
    const { container } = render(<GprDdsWorkspace tab="gpr" onTabChange={onTabChange} gpr={<p>GPR state</p>} dds={<p>DDS state</p>} />);

    expect(screen.getByText("GPR state")).toBeInTheDocument();
    expect(screen.getByText("DDS state")).toBeInTheDocument();
    expect(container.querySelector('[role="tabpanel"][aria-label="ДДС"]')).not.toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "ДДС" }));
    expect(onTabChange).toHaveBeenCalledWith("dds");
  });
});
