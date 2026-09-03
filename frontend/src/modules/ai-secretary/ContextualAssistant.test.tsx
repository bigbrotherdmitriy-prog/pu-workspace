import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContextualAssistant } from "./ContextualAssistant";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const assistant = () => screen.getByRole("button", { name: "Спросить AI Secretary об этом элементе" });

describe("ContextualAssistant", () => {
  it("does not obscure a card or request help while reading or focusing it", () => {
    vi.useFakeTimers();
    const onAsk = vi.fn();
    render(<><article data-ai-help="Проверка задачи"><button>История</button></article><ContextualAssistant section="Задачи" onAsk={onAsk} /></>);
    fireEvent.mouseOver(screen.getByRole("article"));
    act(() => vi.advanceTimersByTime(2000));
    fireEvent.focusIn(screen.getByRole("button", { name: "История" }));
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(document.querySelector(".ai-hover-bubble")).toBeNull();
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("opens help only on explicit activation and preserves hovered context", () => {
    const onAsk = vi.fn();
    render(<><article data-ai-help="Учебная задача"><span>Текст задачи</span></article><ContextualAssistant section="Задачи" onAsk={onAsk} /></>);
    fireEvent.mouseOver(screen.getByText("Текст задачи"));
    fireEvent.mouseOut(screen.getByText("Текст задачи"));
    fireEvent.mouseOver(assistant());
    fireEvent.focusIn(assistant());
    expect(onAsk).not.toHaveBeenCalled();
    fireEvent.click(assistant());
    expect(onAsk).toHaveBeenCalledOnce();
    expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("элементом «Учебная задача»"));
  });

  it("retains keyboard-focused context when focus moves to the native help button", () => {
    const onAsk = vi.fn();
    render(<><input aria-label="Срок задачи" /><ContextualAssistant section="Задачи" onAsk={onAsk} /></>);
    fireEvent.focusIn(screen.getByRole("textbox"));
    act(() => assistant().focus());
    expect(assistant()).toHaveFocus();
    expect(assistant()).toHaveAttribute("type", "button");
    expect(assistant()).not.toBeDisabled();
    fireEvent.click(assistant());
    expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("элементом «Срок задачи»"));
  });

  it("clears stale context after navigation and does not use the whole page as a fallback", () => {
    const onAsk = vi.fn();
    const { rerender } = render(<><article data-ai-help="Старая задача">Старый текст</article><ContextualAssistant section="Задачи" onAsk={onAsk} /></>);
    fireEvent.mouseOver(screen.getByRole("article"));
    rerender(<><p>Личные данные на странице</p><ContextualAssistant section="Документы" onAsk={onAsk} /></>);
    fireEvent.click(assistant());
    const prompt = onAsk.mock.calls[0][0];
    expect(prompt).toContain("разделе «Документы»");
    expect(prompt).not.toContain("Старая задача");
    expect(prompt).not.toContain("Личные данные");
  });

  it("removes document listeners when unmounted", () => {
    const remove = vi.spyOn(document, "removeEventListener");
    const { unmount } = render(<ContextualAssistant section="Задачи" onAsk={vi.fn()} />);
    unmount();
    expect(remove).toHaveBeenCalledWith("mouseover", expect.any(Function));
    expect(remove).toHaveBeenCalledWith("focusin", expect.any(Function));
    remove.mockRestore();
  });
});
