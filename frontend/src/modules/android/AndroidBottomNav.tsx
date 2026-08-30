import { Bot, CalendarDays, FileText, ListTodo, Mail } from "lucide-react";
import "./android.css";

const items = [
  [CalendarDays, "Сегодня"],
  [Mail, "Письма"],
  [FileText, "Документы"],
  [ListTodo, "Задачи"],
  [Bot, "AI Secretary"],
] as const;

type Props = {
  active: string;
  onNavigate: (section: string) => void;
};

export function AndroidBottomNav({ active, onNavigate }: Props) {
  return <nav className="android-bottom-nav" aria-label="Основная мобильная навигация">
    {items.map(([Icon, label]) => <button
      type="button"
      className={active === label ? "active" : ""}
      aria-current={active === label ? "page" : undefined}
      onClick={() => onNavigate(label)}
      key={label}
    >
      <Icon /><span>{label === "AI Secretary" ? "AI" : label}</span>
    </button>)}
  </nav>;
}
