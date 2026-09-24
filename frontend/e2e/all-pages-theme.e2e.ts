import { expect, test, settled } from "./storage-fixtures";

const pages = ["Рабочий центр", "Сегодня", "Письма", "Задачи", "AI Secretary", "Проекты", "Запуск проекта", "Договоры", "Документы", "Центр знаний", "Риски и решения", "Обязательства", "ГПР и ДДС", "ДДС", "Аналитика", "Предложения", "Совещания", "Уведомления", "Интеграции", "Журнал", "Настройки"];

for (const theme of ["light", "dark"]) test(`all sections remain readable in ${theme}`, async ({ page, mock }, info) => {
  test.setTimeout(120000);
  const message = { id: 501, project_id: 2, direction: "incoming", thread_id: "synthetic-501", subject: "График поставки (тест)", sender: "supplier@example.invalid", content: "Синтетическое письмо: согласовать поставку.", summary: "Согласовать график", headers: { to: "operator@example.invalid" }, status: "completed", context_confirmed: true, attachments: [], drafts: [], created_at: "2026-09-05T10:00:00Z" };
  await page.route("**/mail/projects/2/folders", route => route.fulfill({ json: { provider_available: false, provider_error: "temporarily_unavailable", folders: [{ id: "inbox", name: "Входящие", count: 1 }] } }));
  await page.route("**/mail/projects/2/threads?*", route => route.fulfill({ json: { threads: [{ thread_id: message.thread_id, latest: message }], next_cursor: null } }));
  await page.route("**/mail/projects/2/threads/synthetic-501", route => route.fulfill({ json: { thread_id: message.thread_id, messages: [message] } }));
  await page.route("**/projects/2/contracts", route => route.fulfill({ json: { contracts: [{ id: 41, number: "C-41", title: "Монтаж оборудования", contract_kind: "supply" }] } }));
  await page.route("**/execution/overview?*", route => route.fulfill({ json: {
    budget: [], cash_flow: [{ id: 82, contract_id: 41, direction: "outflow", title: "Материалы", planned_date: "2026-09-25", planned_amount: 30000, actual_amount: 0, category: "Прямые", status: "proposed", currency: "RUB" }], procurement: [], acts: [],
    baselines: [{ id: 71, contract_id: 41, name: "ГПР", version: 1, status: "draft" }],
    schedule: [{ id: 72, baseline_id: 71, title: "Монтаж оборудования", planned_start: "2026-09-01", planned_finish: "2026-09-25", planned_progress: 50, actual_progress: 20, status: "planned", sort_order: 1 }],
    summary: { budget_planned: 0, budget_committed: 0, budget_actual: 0, budget_forecast: 0, budget_variance: 0, cash_balance_forecast: -30000, cash_gap: 30000, cash_gap_date: "2026-09-25", delayed_schedule: 0, late_procurement: 0, acts_pending: 0, pending_payments: 1, unlinked_invoices: 0 },
  } }));
  await page.goto("/new/");
  await page.getByRole("button", { name: theme === "dark" ? "Тёмная" : "Светлая", exact: true }).click();
  const results: unknown[] = [];
  for (const [index, name] of pages.entries()) {
    await page.locator("aside nav").getByRole("button", { name: name === "ДДС" ? "ГПР и ДДС" : name, exact: true }).click();
    if (name === "ГПР и ДДС") {
      await page.getByLabel("Договор для графика работ").selectOption("41");
      await page.locator(".gpr-grid tbody tr").first().click();
      const row = await page.locator(".gpr-grid tbody tr").first().boundingBox();
      const chart = await page.locator(".gpr-chart-row").first().boundingBox();
      expect(Math.abs(row!.y - chart!.y), "table and Gantt row align").toBeLessThanOrEqual(1);
      expect(row!.height).toBe(36);
    }
    if (name === "ДДС") await page.getByRole("tab", { name: "ДДС", exact: true }).click();
    await page.waitForLoadState("networkidle");
    await settled(page);
    if (name === "Риски и решения") await expect(page.locator(".governance-overview")).toHaveCSS("display", "grid");
    if (name === "Договоры") await expect(page.locator(".contract-scheme-head")).toHaveCSS("background-color", theme === "dark" ? "rgb(16, 36, 60)" : "rgb(245, 244, 240)");
    if (name === "Интеграции") {
      const head = await page.locator(".integrations-command").boundingBox();
      const copy = await page.locator(".integrations-command p").boundingBox();
      expect(copy!.y + copy!.height).toBeLessThanOrEqual(head!.y + head!.height);
    }
    const issues = await page.evaluate(() => {
      const rgb = (s: string) => (s.match(/[\d.]+/g) || []).map(Number);
      const lum = (c: number[]) => c.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
      return [...document.querySelectorAll<HTMLElement>(".shell *, [role=dialog] *")].flatMap(el => {
        if (!el.checkVisibility() || el.closest("[disabled], [aria-disabled=true], svg, canvas") || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent?.trim())) return [];
        const style = getComputedStyle(el);
        let p: Element | null = el, bg = [255, 255, 255, 1];
        while (p) { const c = rgb(getComputedStyle(p).backgroundColor); if (c.length >= 3 && (c[3] ?? 1) === 1) { bg = c; break; } p = p.parentElement; }
        const fg = rgb(style.color);
        const contrast = (Math.max(lum(fg), lum(bg)) + .05) / (Math.min(lum(fg), lum(bg)) + .05);
        // Conservative smoke gate; decorative text and transparent gradients need visual review too.
        if (contrast >= 4.5) return [];
        return [{ tag: el.tagName, cls: el.className, text: el.textContent?.trim().slice(0, 65), fg: style.color, bg, contrast: +contrast.toFixed(2) }];
      });
    });
    results.push({ name, issues });
    await page.screenshot({ path: info.outputPath(`${index}-${theme}.png`), fullPage: true, animations: "disabled", timeout: 10000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await settled(page);
    if (name === "ГПР и ДДС") {
      await expect(page.locator(".gpr-split")).toHaveCSS("overflow-x", "auto");
      const scrollable = await page.locator(".gpr-split").evaluate(el => { el.scrollLeft = 200; const reached = el.scrollLeft; el.scrollLeft = 0; return reached; });
      expect(scrollable, "mobile Gantt can reach the chart pane").toBeGreaterThan(0);
    }
    expect.soft(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: mobile page must not overflow`).toBe(true);
    await page.screenshot({ path: info.outputPath(`${index}-${theme}-mobile.png`), fullPage: true, animations: "disabled", timeout: 10000 });
    await page.setViewportSize({ width: 1440, height: 1000 });
    if (name === "ГПР и ДДС" || name === "ДДС") await page.getByRole("button", { name: "Закрыть ГПР и ДДС" }).click();
  }
  await info.attach("contrast-audit", { body: JSON.stringify(results, null, 2), contentType: "application/json" });
  expect(results.flatMap((r: any) => r.issues.map((i: any) => ({ page: r.name, ...i })))).toEqual([]);
});

for (const theme of ["light", "dark"]) test(`login and mail settings follow ${theme} theme`, async ({ page, mock: _mock }, info) => {
  await page.addInitScript(theme => localStorage.setItem("pu-display-preferences-v1", JSON.stringify({ theme, comfort: false })), theme);
  await page.route("**/auth/me", route => route.fulfill({ status: 401, json: { detail: "not authenticated" } }));
  await page.goto("/new/");
  await expect(page.locator(".login-card")).toBeVisible();
  const surface = theme === "dark" ? "rgb(16, 36, 60)" : "rgb(245, 244, 240)";
  await expect(page.locator(".login-card")).toHaveCSS("background-color", surface);
  await page.screenshot({ path: info.outputPath(`login-${theme}.png`) });
  await page.unroute("**/auth/me");
  await page.reload();
  await page.locator("aside nav").getByRole("button", { name: "Письма", exact: true }).click();
  await page.locator(".mail-client").getByRole("button", { name: "Настройки", exact: true }).click();
  await expect(page.locator(".mail-settings-dialog")).toHaveCSS("background-color", surface);
  await page.screenshot({ path: info.outputPath(`mail-settings-${theme}.png`), fullPage: true });
});
