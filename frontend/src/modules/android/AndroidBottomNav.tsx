import { BarChart3, LayoutDashboard, ListTodo, Mail, Menu, Plus, Route } from "lucide-react";
import "./android.css";

const items = [
  [LayoutDashboard, "Центр", "Рабочий центр", undefined],
  [ListTodo, "Задачи", "Задачи", undefined],
  [Mail, "Письма", "Письма", undefined],
  [Route, "ГПР", "ГПР и ДДС", "gpr"],
  [BarChart3, "ДДС", "ГПР и ДДС", "dds"],
] as const;

type Props = {
  active: string;
  activeFinanceTab: "gpr" | "dds";
  onNavigate: (section: string, financeTab?: "gpr" | "dds") => void;
  onMore: () => void;
  onUpload: () => void;
};

export function AndroidBottomNav({ active, activeFinanceTab, onNavigate, onMore, onUpload }: Props) {
  return <><button type="button" className="android-upload-fab" aria-label="Добавить документ" onClick={onUpload}><Plus /></button><nav className="android-bottom-nav" aria-label="Основная мобильная навигация">
    {items.map(([Icon, label, section, financeTab]) => <button
      type="button"
      className={active === section && (!financeTab || activeFinanceTab === financeTab) ? "active" : ""}
      aria-current={active === section && (!financeTab || activeFinanceTab === financeTab) ? "page" : undefined}
      onClick={() => onNavigate(section, financeTab)}
      key={label}
    >
      <Icon /><span>{label}</span>
    </button>)}
    <button type="button" onClick={onMore} aria-label="Открыть всё меню"><Menu /><span>Ещё</span></button>
  </nav></>;
}
