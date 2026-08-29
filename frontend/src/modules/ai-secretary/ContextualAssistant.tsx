import { useEffect, useRef, useState } from "react";
import { Bot, Sparkles, X } from "lucide-react";

type Hint = { title: string; advice: string; prompt: string };

const HELP_RULES: { match: RegExp; advice: string }[] = [
  { match: /проект/i, advice: "Проверьте выбранный проект: документы, письма и финансовые записи будут связаны именно с ним." },
  { match: /договор/i, advice: "Выберите файл договора из каталога, проверьте его и затем свяжите ГПР, бюджет, ДДС и акты." },
  { match: /документ|файл|папк|каталог/i, advice: "AI Secretary может объяснить документ, найти обязательства и предложить связи. Оригинал без подтверждения не изменяется." },
  { match: /письм|gmail|входящ/i, advice: "Проверьте проект и договор письма, затем подтвердите задачи, риски и черновик ответа." },
  { match: /задач|обязательств|срок/i, advice: "Уточните ответственного, срок и документ-источник перед подтверждением задачи." },
  { match: /риск|решени/i, advice: "Проверьте основание риска, критичность и ответственное решение." },
  { match: /гпр|график|этап/i, advice: "Используйте черновик baseline, импортируйте этапы и только затем подтверждайте план." },
  { match: /ддс|оплат|сч[её]т|бюджет|финанс/i, advice: "Свяжите договор, этап ГПР, бюджет и счёт. Факт оплаты записывается только после подтверждения пользователя." },
  { match: /интеграц|google|telegram/i, advice: "Проверьте статус подключения. Переподключение не удаляет уже импортированные данные проекта." },
  { match: /обнов|синхрон/i, advice: "Обновление повторно получает актуальное состояние и не должно изменять оригиналы." },
  { match: /создать|добавить|подтверд/i, advice: "Перед действием проверьте проект, источник и последствия. Значимые изменения сохраняются в журнале." },
];

function labelFor(element: HTMLElement, section: string) {
  const accessible = element.dataset.aiHelp || element.getAttribute("aria-label") || element.getAttribute("title") || element.getAttribute("placeholder");
  const ownText = element.innerText?.replace(/\s+/g, " ").trim();
  return (accessible || ownText || section || "Этот элемент").slice(0, 140);
}

function hintFor(element: HTMLElement, section: string): Hint {
  const title = labelFor(element, section);
  const rule = HELP_RULES.find(({ match }) => match.test(`${section} ${title}`));
  return {
    title,
    advice: rule?.advice || "AI Secretary объяснит этот элемент, проверит контекст и подскажет безопасный следующий шаг.",
    prompt: `Я нахожусь в разделе «${section}» и работаю с элементом «${title}». Объясни его назначение, проверь возможные риски и предложи следующий безопасный шаг. Ничего не изменяй без моего подтверждения.`,
  };
}

export function ContextualAssistant({ section, onAsk }: { section: string; onAsk: (prompt: string) => void }) {
  const [hint, setHint] = useState<Hint | null>(null);
  const [hidden, setHidden] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const cancel = () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = null;
    };
    const show = (event: Event) => {
      const raw = event.target;
      if (!(raw instanceof HTMLElement) || raw.closest("[data-ai-overlay]")) return;
      const target = raw.closest<HTMLElement>("[data-ai-help],button,input,select,textarea,article,.metric,.card");
      if (!target) return;
      cancel();
      timerRef.current = window.setTimeout(() => {
        setHidden(false);
        setHint(hintFor(target, section));
      }, event.type === "focusin" ? 0 : 450);
    };
    document.addEventListener("mouseover", show);
    document.addEventListener("focusin", show);
    return () => {
      cancel();
      document.removeEventListener("mouseover", show);
      document.removeEventListener("focusin", show);
    };
  }, [section]);

  if (hidden || !hint) return <button data-ai-overlay className="ai-context-chip" onClick={() => { setHidden(false); setHint(hintFor(document.body, section)); }} title="Контекстная помощь AI Secretary">
    <Bot /> <span>AI-помощь</span>
  </button>;

  return <aside data-ai-overlay className="ai-context-panel" aria-live="polite">
    <div className="ai-context-head">
      <span><Sparkles /> AI Secretary</span>
      <button className="icon" onClick={() => setHidden(true)} aria-label="Скрыть контекстную подсказку"><X /></button>
    </div>
    <strong>{hint.title}</strong>
    <p>{hint.advice}</p>
    <button onClick={() => onAsk(hint.prompt)}>Спросить подробнее</button>
    <small>Наведение ничего не изменяет и не отправляет содержимое во внешний AI.</small>
  </aside>;
}
