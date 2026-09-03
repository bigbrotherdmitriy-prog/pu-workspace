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
    const remember = (event: MouseEvent | FocusEvent) => {
      const raw = event.target;
      if (!(raw instanceof HTMLElement) || raw.closest("[data-ai-overlay]")) return;
      const target = raw.closest<HTMLElement>("[data-ai-help],button,input,select,textarea,article,.metric,.card");
      if (target) contextRef.current = labelFor(target, section);
    };
    // Observe context without opening anything over the document or focused control.
    // Focusing/clicking the mascot preserves the last element selected by mouse or keyboard.
    document.addEventListener("mouseover", remember);
    document.addEventListener("focusin", remember);
    return () => {
      document.removeEventListener("mouseover", remember);
      document.removeEventListener("focusin", remember);
    };
  }, [section]);

  const ask = () => {
    const context = contextRef.current;
    onAsk(`Я нахожусь в разделе «${section}»${context ? ` и работаю с элементом «${context}»` : ""}. Объясни назначение и предложи безопасный следующий шаг. Ничего не изменяй без моего подтверждения.`);
  };

  return <button type="button" data-ai-overlay className="ai-mascot" onClick={ask} aria-label="Спросить AI Secretary об этом элементе" title="AI Secretary — спросить подробнее">
    <span className="ai-mascot-antenna" />
    <span className="ai-mascot-face"><i /><i /></span>
    <span className="ai-mascot-body">PU</span>
  </button>;
}
