import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectIsometric } from "./ProjectIsometric";

afterEach(cleanup);

describe("ProjectIsometric navigation", () => {
  it("renders the approved realistic model and exposes only GPR and DDS actions", () => {
    const openSchedule = vi.fn();
    const openFinance = vi.fn();
    render(<ProjectIsometric onDocuments={vi.fn()} onSchedule={openSchedule} onFinance={openFinance} />);

    fireEvent.click(screen.getByRole("button", { name: "ГПР" }));
    fireEvent.click(screen.getByRole("button", { name: "ДДС" }));

    expect(openSchedule).toHaveBeenCalledOnce();
    expect(openFinance).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /Документы/ })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Вращающаяся модель центра обработки данных" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Модель центра обработки данных" })).toBeInTheDocument();
  });
});
