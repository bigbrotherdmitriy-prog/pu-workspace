import { expect, test, settled } from "./storage-fixtures";

const pages = ["Рабочий центр", "Сегодня", "Письма", "Задачи", "AI Secretary", "Проекты", "Запуск проекта", "Договоры", "Документы", "Центр знаний", "Риски и решения", "Обязательства", "ГПР и ДДС", "ДДС", "Аналитика", "Предложения", "Совещания", "Уведомления", "Интеграции", "Журнал", "Настройки"];

// Dark themes in the shell: the base "Тёмная" and the future-preview neon theme.
// "По времени" is the same widget used for both day and night scheduling and is
// covered by the base dark pass, so it is not audited as a separate theme here.
const darkThemes: { key: string; label: string }[] = [
  { key: "dark", label: "Тёмная" },
  { key: "neon", label: "Неоновая" },
];

for (const theme of darkThemes) for (const name of pages) {
  test(`contrast audit: ${theme.key} / ${name}`, async ({ page, mock }, info) => {
    test.setTimeout(theme.key === "neon" ? 180000 : 120000);
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
    await page.locator("aside nav").getByRole("button", { name: "Сегодня", exact: true }).click();
    await page.getByRole("button", { name: theme.label, exact: true }).click();
    await page.locator("aside nav").getByRole("button", { name: name === "ДДС" ? "ГПР и ДДС" : name, exact: true }).click();
    if (name === "ГПР и ДДС") {
      await page.getByLabel("Договор для графика работ").selectOption("41");
      await page.locator(".gpr-grid tbody tr").first().click();
    }
    if (name === "ДДС") await page.getByRole("tab", { name: "ДДС", exact: true }).click();
    if (name === "Договоры") {
      const workspace = page.locator(".contract-workspace");
      if (await workspace.count() > 0) {
        const openRow = workspace.locator(".contract-register-open").first();
        if (await openRow.count() > 0) await openRow.click();
      }
    }
    await page.waitForLoadState("networkidle");
    await settled(page);

    const issues = await page.evaluate(() => {
      type Violation = {
        selector: string;
        text: string;
        fg: string;
        bg: string;
        ratio: number;
      };
      const parseColor = (value: string): number[] => {
        const match = value.match(/rgba?\(([^)]+)\)/);
        if (!match) return [0, 0, 0, 0];
        const parts = match[1].split(/[,\s/]+/).filter(Boolean).map(Number);
        const [r = 0, g = 0, b = 0, a = 1] = parts;
        return [r, g, b, a];
      };
      const luminance = ([r, g, b]: number[]): number => {
        const channel = (v: number) => {
          const s = v / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      };
      const contrastRatio = (fg: number[], bg: number[]): number => {
        const l1 = luminance(fg);
        const l2 = luminance(bg);
        const [lo, hi] = l1 < l2 ? [l1, l2] : [l2, l1];
        return (hi + 0.05) / (lo + 0.05);
      };
      const composite = (fg: number[], bg: number[]): number[] => {
        const a = fg[3] ?? 1;
        return [
          fg[0] * a + bg[0] * (1 - a),
          fg[1] * a + bg[1] * (1 - a),
          fg[2] * a + bg[2] * (1 - a),
          1,
        ];
      };
      const cssEscape = (value: string): string => {
        if (typeof (window as any).CSS?.escape === "function") return (window as any).CSS.escape(value);
        return value.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
      };
      const selectorFor = (el: Element): string => {
        const parts: string[] = [];
        let node: Element | null = el;
        let depth = 0;
        while (node && node !== document.documentElement && depth < 3) {
          const tag = node.tagName.toLowerCase();
          const classes = Array.from(node.classList).slice(0, 3).map(cls => `.${cssEscape(cls)}`).join("");
          parts.unshift(`${tag}${classes}`);
          node = node.parentElement;
          depth += 1;
        }
        return parts.join(" > ");
      };
      // Effective background resolution:
      //   walk up from the element (inclusive). Collect translucent layers.
      //   The first ancestor whose background-color is opaque enough (alpha >= 0.5)
      //   becomes the base. Only if we encounter a background-image before that
      //   opaque layer, the true background is unknown — report as unknown so the
      //   caller can skip. Translucent layers above the base are composited onto
      //   the base color.
      const resolveBackground = (start: Element): { bg: number[] | null; unknown: boolean } => {
        const layers: number[][] = [];
        let node: Element | null = start;
        let sawImage = false;
        while (node) {
          const style = getComputedStyle(node);
          const color = parseColor(style.backgroundColor);
          if (color[3] >= 0.5) {
            if (sawImage) return { bg: null, unknown: true };
            let bg: number[] = [color[0], color[1], color[2], 1];
            for (const layer of layers.reverse()) bg = composite(layer, bg);
            return { bg, unknown: false };
          }
          if (style.backgroundImage && style.backgroundImage !== "none") {
            sawImage = true;
          }
          if (color[3] > 0) layers.push(color);
          node = node.parentElement;
        }
        // No opaque ancestor found: fall back to white base with translucent layers composited on top.
        let bg: number[] = [255, 255, 255, 1];
        for (const layer of layers.reverse()) bg = composite(layer, bg);
        return { bg, unknown: false };
      };
      const root = document.querySelector(".shell") || document.body;
      const seen = new Set<Element>();
      const violations: Violation[] = [];

      const checkElement = (el: Element, content: string) => {
        if (!content) return;
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return;
        const style = getComputedStyle(el);
        if (style.visibility === "hidden" || style.display === "none") return;
        if (el.closest("[disabled], [aria-disabled=true]")) return;
        const resolved = resolveBackground(el);
        if (resolved.unknown || !resolved.bg) return;
        const bg = resolved.bg;
        const fgRaw = parseColor(style.color);
        const fg = fgRaw[3] >= 1 ? fgRaw : composite(fgRaw, bg);
        const ratio = contrastRatio(fg, bg);
        if (ratio < 3.0) {
          violations.push({
            selector: selectorFor(el),
            text: content.slice(0, 40),
            fg: `rgb(${Math.round(fg[0])}, ${Math.round(fg[1])}, ${Math.round(fg[2])})`,
            bg: `rgb(${Math.round(bg[0])}, ${Math.round(bg[1])}, ${Math.round(bg[2])})`,
            ratio: Number(ratio.toFixed(2)),
          });
        }
      };

      // 1. Text nodes across the shell.
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let text: Node | null = walker.nextNode();
      while (text) {
        const content = text.textContent?.trim() ?? "";
        if (content.length <= 1) { text = walker.nextNode(); continue; }
        const parent = text.parentElement;
        if (!parent || seen.has(parent)) { text = walker.nextNode(); continue; }
        seen.add(parent);
        checkElement(parent, content);
        text = walker.nextNode();
      }

      // 2. Form controls: audit their value / visible text as well.
      root.querySelectorAll("input, select, button").forEach(el => {
        if (seen.has(el)) return;
        seen.add(el);
        const tag = el.tagName.toLowerCase();
        let content = "";
        if (tag === "input") {
          const input = el as HTMLInputElement;
          const type = (input.type || "text").toLowerCase();
          if (type === "hidden" || type === "checkbox" || type === "radio" || type === "range" || type === "file" || type === "submit" || type === "button" || type === "reset" || type === "image") return;
          content = (input.value ?? input.placeholder ?? "").trim();
          if (!content && input.placeholder) content = input.placeholder.trim();
        } else if (tag === "select") {
          const select = el as HTMLSelectElement;
          const selected = select.selectedOptions[0];
          content = (selected?.textContent ?? select.value ?? "").trim();
        } else {
          content = (el.textContent ?? "").trim();
        }
        if (!content) return;
        checkElement(el, content);
      });

      return violations;
    });

    const reportPath = test.info().outputPath(`theme-contrast-${theme.key}-${encodeURIComponent(name)}.json`);
    await info.attach(`theme-contrast-${theme.key}-${name}`, {
      body: JSON.stringify({ theme: theme.key, page: name, violations: issues }, null, 2),
      contentType: "application/json",
    });
    const fs = await import("node:fs/promises");
    const path = await import("node:path");
    const dir = path.dirname(reportPath);
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(reportPath, JSON.stringify({ theme: theme.key, page: name, violations: issues }, null, 2), "utf8");

    const uniqueSelectors = Array.from(new Set(issues.map(item => item.selector))).slice(0, 30);
    expect(issues, `theme-contrast ${theme.key} / ${name}: ${issues.length} violations\n${uniqueSelectors.join("\n")}`).toHaveLength(0);
  });
}
