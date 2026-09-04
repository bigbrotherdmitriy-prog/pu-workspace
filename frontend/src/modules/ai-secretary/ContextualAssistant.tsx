import { useEffect, useRef } from "react";

function labelFor(element: HTMLElement, section: string) {
  const accessible = element.dataset.aiHelp || element.getAttribute("aria-label") || element.getAttribute("title") || element.getAttribute("placeholder");
  const ownText = element.innerText?.replace(/\s+/g, " ").trim();
  return (accessible || ownText || section || "Этот элемент").slice(0, 90);
}

export function ContextualAssistant({ section, onAsk }: { section: string; onAsk: (prompt: string) => void }) {
  const contextRef = useRef<string | null>(null);

  useEffect(() => {
    contextRef.current = null;
    let focusContext = false;
    let pointerPosition: { x: number; y: number } | null = null;
    const remember = (event: MouseEvent | FocusEvent) => {
      const raw = event.target;
      if (!(raw instanceof HTMLElement) || raw.closest("[data-ai-overlay]")) return;
      const target = raw.closest<HTMLElement>("[data-ai-help],button,input,select,textarea,article,.metric,.card");
      if (target) contextRef.current = labelFor(target, section);
    };
    const focus = (event: FocusEvent) => {
      remember(event);
      focusContext = true;
    };
    const hover = (event: MouseEvent) => {
      // Scrolling a focused control into view can fire mouseover beneath a
      // stationary pointer. Keep keyboard context until the pointer really moves.
      if (focusContext) return;
      pointerPosition = { x: event.clientX, y: event.clientY };
      remember(event);
    };
    const move = (event: MouseEvent) => {
      const moved = !pointerPosition || event.clientX !== pointerPosition.x || event.clientY !== pointerPosition.y;
      pointerPosition = { x: event.clientX, y: event.clientY };
      if (!moved) return;
      focusContext = false;
      remember(event);
    };
    // Observe context without opening anything over the document or focused control.
    // Focusing/clicking the mascot preserves the last element selected by mouse or keyboard.
    document.addEventListener("mouseover", hover);
    document.addEventListener("mousemove", move);
    document.addEventListener("focusin", focus);
    return () => {
      document.removeEventListener("mouseover", hover);
      document.removeEventListener("mousemove", move);
      document.removeEventListener("focusin", focus);
    };
  }, [section]);

  const ask = () => {
    const context = contextRef.current;
    onAsk(`Я нахожусь в разделе «${section}»${context ? ` и работаю с элементом «${context}»` : ""}. Объясни назначение и предложи безопасный следующий шаг. Ничего не изменяй без моего подтверждения.`);
  };

  return <button type="button" data-ai-overlay className="ai-mascot" onClick={ask} aria-label="Спросить AI Secretary об этом элементе">
    <span className="ai-mascot-antenna" />
    <span className="ai-mascot-face"><i /><i /></span>
    <span className="ai-mascot-body">PU</span>
  </button>;
}
