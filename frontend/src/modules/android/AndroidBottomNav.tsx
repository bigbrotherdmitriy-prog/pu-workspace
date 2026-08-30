import { Bot, CalendarDays, FileText, ListTodo, Mail, Plus } from "lucide-react";
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
  onUpload: () => void;
};

export function AndroidBottomNav({ active, onNavigate, onUpload }: Props) {
  return <><button type="button" className="android-upload-fab" aria-label="Добавить документ" onClick={onUpload}><Plus /></button><nav className="android-bottom-nav" aria-label="Основная мобильная навигация">
    {items.map(([Icon, label]) => <button
      type="button"
      className={active === label ? "active" : ""}
      aria-current={active === label ? "page" : undefined}
      onClick={() => onNavigate(label)}
      key={label}
    >
      <Icon /><span>{label === "AI Secretary" ? "AI" : label}</span>
    </button>)}
  </nav></>;
}
