import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectIsometric } from "./modules/dashboard/ProjectIsometric";
import "./styles.css";
import "./designer.css";
import "./interface-v9-control-room.css";
import "./project-hq-theme.css";
import "./future-light-dashboard.css";

const nav = ["Рабочий центр", "Сегодня", "Письма", "Задачи", "AI Secretary", "Проекты", "Запуск проекта", "Договоры", "Документы", "Центр знаний", "Риски и решения", "Обязательства", "ГПР", "Бюджет и ДДС", "Аналитика"];
// «Требуют внимания» и «Ждут решения» убраны из ряда: они дублируют карточку «Требует решения».
const metrics = [["Обязательства", "7"], ["Просрочено", "2"], ["Риски", "0"], ["Уведомления", "0"], ["Бюджет освоен", "86%"]];
const focusItems = [["Просроченные задачи", "1"], ["Открытые риски", "0"], ["Ждут решения", "1"]];
const decisionsTotal = focusItems.reduce((sum, [label, value]) => label === "Открытые риски" ? sum : sum + Number(value), 0);

const icons: Record<string, React.ReactElement> = {
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  home: <path d="M4 11 12 4l8 7v9h-5v-6H9v6H4z" />,
  tasks: <path d="M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m4 7 8 6 8-6" /></>,
  gpr: <path d="M4 6h9M7 12h10M10 18h10" />,
  dds: <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
};
const Icon = ({ name }: { name: string }) => <svg className="fl-icon" viewBox="0 0 24 24" aria-hidden="true">{icons[name]}</svg>;
const tabs: [string, string, string][] = [["home", "Центр", "Рабочий центр"], ["tasks", "Задачи", "Задачи"], ["mail", "Письма", "Письма"], ["gpr", "ГПР", "ГПР"], ["dds", "ДДС", "Бюджет и ДДС"]];

function Preview() {
  const [active, setActive] = useState("Рабочий центр");
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const go = (item: string) => { setActive(item); setMenuOpen(false); };

  // Параллакс глубины: слои фона смещаются за курсором/наклоном с разной силой — эффект объёма.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const root = document.querySelector<HTMLElement>(".future-preview-shell");
    if (!root) return;
    let tx = 0, ty = 0, x = 0, y = 0, frame = 0;
    const onMove = (event: PointerEvent) => { tx = (event.clientX / window.innerWidth) * 2 - 1; ty = (event.clientY / window.innerHeight) * 2 - 1; };
    const onTilt = (event: DeviceOrientationEvent) => { if (event.gamma == null || event.beta == null) return; tx = Math.max(-1, Math.min(1, event.gamma / 25)); ty = Math.max(-1, Math.min(1, (event.beta - 45) / 25)); };
    const tick = () => {
      x += (tx - x) * 0.06; y += (ty - y) * 0.06;
      root.style.setProperty("--mx", x.toFixed(4)); root.style.setProperty("--my", y.toFixed(4));
      frame = requestAnimationFrame(tick);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("deviceorientation", onTilt);
    tick();
    return () => { cancelAnimationFrame(frame); window.removeEventListener("pointermove", onMove); window.removeEventListener("deviceorientation", onTilt); };
  }, []);

  return <div className={`future-preview-shell future-light-dashboard${menuOpen ? " menu-open" : ""}`}>
    <div className="fl-depth" aria-hidden="true"><div className="fl-depth-far" /><div className="fl-depth-beams" /><div className="fl-depth-grid" /><div className="fl-depth-dust" /></div>
    <aside className="future-preview-sidebar" id="fl-sidebar" aria-label="Главное меню">
      <div className="future-preview-brand"><b>PU</b><span><strong>PU Workspace</strong><small>Project intelligence</small></span>
        <button className="fl-icon-button fl-sidebar-close" aria-label="Закрыть меню" onClick={() => setMenuOpen(false)}><Icon name="close" /></button>
      </div>
      <nav>{nav.map((item) => <button className={item === active ? "active" : ""} aria-current={item === active ? "page" : undefined} key={item} onClick={() => go(item)}><i />{item}</button>)}</nav>
      <small className="fl-system-status">● Все системы онлайн</small>
    </aside>
    {menuOpen && <div className="fl-backdrop" onClick={() => setMenuOpen(false)} />}
    <main>
      <header className="future-preview-header">
        <button className="fl-icon-button fl-burger" aria-label="Открыть меню" aria-expanded={menuOpen} aria-controls="fl-sidebar" onClick={() => setMenuOpen(true)}><Icon name="menu" /></button>
        <div className="fl-header-title"><small>ОПЕРАТИВНЫЙ КОНТУР</small><button className="fl-project-mobile">Модернизация ЦОД ▾</button><h1>Рабочий центр</h1></div>
        <div className="fl-header-actions"><button>Поиск Ctrl K</button><button>Проект · Модернизация ЦОД ▾</button><b>● Онлайн</b></div>
        <div className="fl-header-actions-mobile"><button className="fl-icon-button" aria-label="Поиск"><Icon name="search" /></button><i className="fl-online-dot" aria-label="Онлайн" /></div>
      </header>
      <section className="future-preview-content">
        <div className="future-preview-sync"><span className="fl-sync-text"><strong>✓ Синхронизация готова</strong><span>Drive · Gmail · Tasks · Calendar</span></span><button>Синхронизировать</button></div>
        <section className="dashboard-overview-deck">
          <div className="dashboard-hero">
            <div className="dashboard-hero-copy">
              <span className="dashboard-kicker">AI Secretary · контекст собран</span>
              <h2>Штаб управления проектом</h2>
              <p>Подтвердить исполнителя и новый срок либо завершить задачу.</p>
              <div className="future-preview-insight"><small>КРАТКАЯ СВОДКА</small><p>{decisionsTotal} контрольных пункта требуют решения. Кассовых разрывов нет.</p></div>
              <div className="dashboard-hero-actions"><button>Открыть план дня</button><button>Спросить AI</button></div>
            </div>
            <ProjectIsometric onDocuments={() => go("Документы")} onSchedule={() => go("ГПР")} onFinance={() => go("Бюджет и ДДС")} />
            <aside className="fl-focus" aria-label="Требует решения">
              <span className="fl-focus-kicker">ТРЕБУЕТ РЕШЕНИЯ</span>
              <div className="fl-focus-total"><strong>{decisionsTotal}</strong><span>контрольных пункта</span></div>
              <ul>{focusItems.map(([label, value]) => <li key={label}><span>{label}</span><b>{value}</b></li>)}</ul>
              <button className="fl-focus-action">Разобрать сейчас →</button>
            </aside>
          </div>
          <div className="metrics dashboard-metrics fl-metrics">{metrics.map(([label, value]) => <button key={label}><span>{label}</span><strong>{value}</strong></button>)}</div>
        </section>
        <div className="future-preview-cards"><article><small>КОНТРОЛЬ</small><strong>Что требует внимания</strong><span>{decisionsTotal} пункта требуют проверки</span></article><article><small>БЫСТРЫЙ ДОСТУП</small><strong>Рабочие сценарии</strong><span>Письма · задачи · контур проекта</span></article><article><small>AI SECRETARY</small><strong>Контекст проекта собран</strong><span>41 черновик · 0 уведомлений</span></article></div>
      </section>
    </main>
    <nav className="fl-tabbar" aria-label="Быстрая навигация">
      {tabs.map(([icon, label, target]) => <button key={label} className={active === target ? "active" : ""} aria-current={active === target ? "page" : undefined} onClick={() => go(target)}><Icon name={icon} /><span>{label}</span></button>)}
      <button onClick={() => setMenuOpen(true)} aria-label="Всё меню"><Icon name="more" /><span>Ещё</span></button>
    </nav>
  </div>;
}

createRoot(document.getElementById("preview-root")!).render(<React.StrictMode><Preview /></React.StrictMode>);
