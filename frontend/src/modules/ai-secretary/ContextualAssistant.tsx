import { useEffect, useRef, useState } from "react";

type Hint = { title: string; advice: string; prompt: string };
type Point = { x: number; y: number };

const HELP_RULES: { match: RegExp; advice: string }[] = [
  { match: /проект/i, advice: "Все новые связи относятся к выбранному проекту." },
  { match: /договор/i, advice: "Выберите файл договора, затем свяжите ГПР, бюджет и ДДС." },
  { match: /документ|файл|папк|каталог/i, advice: "Можно проверить содержание и связи, не меняя оригинал." },
  { match: /письм|gmail|входящ/i, advice: "Проверьте проект и договор до подтверждения задач и ответа." },
  { match: /задач|обязательств|срок/i, advice: "Проверьте ответственного, срок и документ-источник." },
  { match: /риск|решени/i, advice: "Проверьте основание, критичность и ответственное решение." },
  { match: /гпр|график|этап/i, advice: "Сначала черновик baseline, затем этапы и подтверждение плана." },
  { match: /ддс|оплат|сч[её]т|бюджет|финанс/i, advice: "Свяжите договор, этап и бюджет; факт оплаты подтверждает пользователь." },
  { match: /интеграц|google|telegram/i, advice: "Переподключение не удаляет уже импортированные данные." },
  { match: /обнов|синхрон/i, advice: "Получает актуальное состояние, не изменяя оригиналы." },
  { match: /создать|добавить|подтверд/i, advice: "Проверьте проект и источник перед подтверждением." },
];

function labelFor(element: HTMLElement, section: string) {
  const accessible = element.dataset.aiHelp || element.getAttribute("aria-label") || element.getAttribute("title") || element.getAttribute("placeholder");
  const ownText = element.innerText?.replace(/\s+/g, " ").trim();
  return (accessible || ownText || section || "Этот элемент").slice(0, 90);
}

function hintFor(element: HTMLElement, section: string): Hint {
  const title = labelFor(element, section);
  const rule = HELP_RULES.find(({ match }) => match.test(`${section} ${title}`));
  return {
    title,
    advice: rule?.advice || "Наведите курсор — я кратко объясню элемент и безопасный следующий шаг.",
    prompt: `Я нахожусь в разделе «${section}» и работаю с элементом «${title}». Объясни назначение и предложи безопасный следующий шаг. Ничего не изменяй без моего подтверждения.`,
  };
}

function bubblePosition(point: Point) {
  const width = 290;
  const left = Math.min(point.x + 18, window.innerWidth - width - 12);
  const top = Math.min(point.y + 18, window.innerHeight - 130);
  return { left: Math.max(12, left), top: Math.max(12, top) };
}

export function ContextualAssistant({ section, onAsk }: { section: string; onAsk: (prompt: string) => void }) {
  const [hint, setHint] = useState<Hint | null>(null);
  const [point, setPoint] = useState<Point>({ x: 0, y: 0 });
  const timerRef = useRef<number | null>(null);
  const leaveRef = useRef<number | null>(null);

  useEffect(() => {
    const cancel = () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      if (leaveRef.current !== null) window.clearTimeout(leaveRef.current);
      timerRef.current = null;
      leaveRef.current = null;
    };
    const show = (event: MouseEvent | FocusEvent) => {
      const raw = event.target;
      if (!(raw instanceof HTMLElement) || raw.closest("[data-ai-overlay]")) return;
      const target = raw.closest<HTMLElement>("[data-ai-help],button,input,select,textarea,article,.metric,.card");
      if (!target) return;
      cancel();
      const rect = target.getBoundingClientRect();
      setPoint("clientX" in event ? { x: event.clientX, y: event.clientY } : { x: rect.right, y: rect.top });
      timerRef.current = window.setTimeout(() => setHint(hintFor(target, section)), event.type === "focusin" ? 0 : 550);
    };
    const hide = (event: MouseEvent) => {
      const next = event.relatedTarget;
      if (next instanceof HTMLElement && next.closest("[data-ai-overlay]")) return;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      leaveRef.current = window.setTimeout(() => setHint(null), 120);
    };
    document.addEventListener("mouseover", show);
    document.addEventListener("focusin", show);
    document.addEventListener("mouseout", hide);
    return () => {
      cancel();
      document.removeEventListener("mouseover", show);
      document.removeEventListener("focusin", show);
      document.removeEventListener("mouseout", hide);
    };
  }, [section]);

  const current = hint || hintFor(document.body, section);
  const position = bubblePosition(point);
  return <>
    {hint && <div data-ai-overlay className="ai-hover-bubble" style={{ left: position.left, top: position.top }} role="tooltip">
      <strong>{hint.title}</strong>
      <span>{hint.advice}</span>
      <small>Нажмите робота, если нужно подробнее</small>
    </div>}
    <button data-ai-overlay className="ai-mascot" onClick={() => onAsk(current.prompt)} aria-label="Спросить AI Secretary об этом элементе" title="AI Secretary — спросить подробнее">
      <span className="ai-mascot-antenna" />
      <span className="ai-mascot-face"><i /><i /></span>
      <span className="ai-mascot-body">PU</span>
    </button>
  </>;
}
