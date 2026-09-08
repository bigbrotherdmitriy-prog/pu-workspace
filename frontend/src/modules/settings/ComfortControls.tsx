import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "auto";
type Preference = { theme: Theme; comfort: boolean };
const key = "pu-display-preferences-v1";

function readPreference(): Preference {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || "null");
    if (value && typeof value === "object" && "theme" in value && "comfort" in value
      && (value.theme === "light" || value.theme === "dark" || value.theme === "auto")
      && typeof value.comfort === "boolean") return { theme: value.theme, comfort: value.comfort };
  } catch { /* Display preferences are optional in restricted browsers. */ }
  return { theme: "light", comfort: false };
}

export function resolvedTheme(theme: Theme, hour: number): "light" | "dark" {
  return theme === "auto" ? (hour >= 8 && hour < 20 ? "light" : "dark") : theme;
}

export function applyDisplayPreference() {
  const preference = readPreference();
  document.documentElement.dataset.displayTheme = resolvedTheme(preference.theme, new Date().getHours());
  document.documentElement.dataset.comfort = String(preference.comfort);
}

export function ComfortControls() {
  const [preference, setPreference] = useState(readPreference);
  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.displayTheme = resolvedTheme(preference.theme, new Date().getHours());
      document.documentElement.dataset.comfort = String(preference.comfort);
    };
    apply();
    try { localStorage.setItem(key, JSON.stringify(preference)); } catch { /* Keep session settings usable. */ }
    const timer = window.setInterval(apply, 30_000);
    document.addEventListener("visibilitychange", apply);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", apply); };
  }, [preference]);
  return <div className="display-preferences" role="group" aria-label="Комфорт чтения">
    <div className="display-theme-choices" role="group" aria-label="Тема интерфейса">
      {([["light", "Светлая"], ["dark", "Тёмная"], ["auto", "По времени"]] as const).map(([theme, label]) =>
        <button key={theme} type="button" aria-pressed={preference.theme === theme}
          onClick={() => setPreference({ ...preference, theme })}>{label}</button>)}
    </div>
    <button type="button" aria-pressed={preference.comfort}
      onClick={() => setPreference({ ...preference, comfort: !preference.comfort })}>Режим комфорта</button>
    {preference.theme === "auto" && <small>Светлая с 08:00 до 20:00 · время устройства</small>}
  </div>;
}
