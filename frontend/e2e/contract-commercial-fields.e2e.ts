import { expect, test } from "./storage-fixtures";

// Synthetic, deny-by-default browser gate. It exercises the real App editor,
// never a live contract, and records screenshots only at absolute D: paths.
const preciseAmount = "1234567890123456.78";
// Local QA passes an absolute D: directory; CI keeps portable Playwright outputs.
const outputDirectory = (globalThis as typeof globalThis & {
  process?: { env?: Record<string, string | undefined> };
}).process?.env?.V6_10A_SCREENSHOT_DIR;

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`common contract card preserves exact commercial fields and a rejected draft at ${viewport.width}`, async ({ page, mock }, info) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width: 1440, height: 1000 });
    const contract: Record<string, unknown> = {
      id: 141, record_version: 5, number: "SYN-V6-10A", title: "Вымышленный договор для проверки",
      counterparty: "Вымышленный контрагент", contract_kind: "customer", parent_contract_id: null,
      status: "active", amount: null, advance_amount: "0.00", retention_percent: "22.00",
      vat_mode: "unspecified", vat_rate: null, signed_at: null, performed_from: null,
      performed_to: null, warranty_until: null, linked_documents: [], version_history: [],
      budget_proposals: [], analysis: { source_ready: false, tasks: 0, obligations: 0, risks: 0, decisions: 0 },
    };
    const patches: Record<string, unknown>[] = [];
    await page.route("**/projects/2/contracts", route => route.fulfill({ contentType: "application/json",
      body: JSON.stringify({ contracts: [contract] }) }));
    await page.route("**/projects/2/contracts/141", async route => {
      const method = route.request().method();
      mock.requests.push({ method, path: "/projects/2/contracts/141", body: route.request().postData() });
      expect(method).toBe("PATCH");
      const payload = JSON.parse(route.request().postData() || "{}") as Record<string, unknown>;
      patches.push(payload);
      if (patches.length === 1) {
        expect(payload).toMatchObject({ expected_record_version: 5, amount: preciseAmount,
          advance_amount: "0", retention_percent: "22.00", vat_mode: "rate", vat_rate: "0",
          signed_at: "2026-09-01", performed_from: "2026-09-02", performed_to: "2026-12-31",
          warranty_until: "2027-12-31" });
        const { expected_record_version: _expected, ...fields } = payload;
        Object.assign(contract, fields, { record_version: 6, vat_rate: "0.00" });
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(contract) });
      }
      expect(payload).toMatchObject({ expected_record_version: 6, amount: preciseAmount,
        advance_amount: "1234567890123456.79", vat_mode: "rate", vat_rate: "0.00" });
      return route.fulfill({ status: 422, contentType: "application/json",
        headers: { "X-Request-ID": "synthetic-commercial-422" },
        body: JSON.stringify({ detail: [{ loc: ["body", "advance_amount"],
          msg: "Аванс не может превышать сумму договора", type: "value_error" }] }) });
    });

    await page.goto("/new/");
    await expect(page.getByLabel("Текущий проект")).toHaveValue("2");
    await page.locator('aside button[title="Договоры"]').click();
    // Initial navigation is common to both runs; the card/editor use the target viewport.
    await page.setViewportSize(viewport);
    const row = page.locator(".contract-register-row").filter({ hasText: "SYN-V6-10A" });
    await row.locator(".contract-register-open").click();
    const card = page.getByRole("article", { name: "Карточка договора SYN-V6-10A", exact: true });
    await expect(card).toHaveCount(1);
    await expect(page.locator(".contract-advanced-list")).toHaveCount(0);
    const summary = card.locator(".contract-commercial-summary");
    await expect(summary.locator("div").filter({ has: page.getByText("Сумма с НДС", { exact: true }) })).toContainText("Не указана");
    await expect(summary.locator("div").filter({ has: page.getByText("Аванс с НДС", { exact: true }) })).toContainText("0,00 ₽");
    await expect(summary.locator("div").filter({ has: page.getByText("НДС", { exact: true }) })).toContainText("НДС не указан");
    await expect(card.getByRole("note")).toContainText("будет добавлена в V6-10b");

    await card.getByRole("button", { name: "Редактировать", exact: true }).click();
    const form = page.getByRole("form", { name: "Редактирование договора SYN-V6-10A" });
    await form.getByLabel("Сумма редактируемого договора с НДС, ₽").fill(preciseAmount);
    await form.getByLabel("Аванс редактируемого договора с НДС, ₽").fill("0");
    await form.getByLabel("НДС договора", { exact: true }).selectOption("rate");
    await form.getByLabel("Ставка НДС, %", { exact: true }).fill("0");
    await form.getByLabel("Дата подписания редактируемого договора").fill("2026-09-01");
    await form.getByLabel("Начало исполнения договора").fill("2026-09-02");
    await form.getByLabel("Окончание исполнения договора").fill("2026-12-31");
    await form.getByLabel("Гарантия до", { exact: true }).fill("2027-12-31");
    await form.getByRole("button", { name: "Сохранить изменения" }).click();
    await expect(form).toHaveCount(0);
    await expect(card).toContainText("Версия карточки: 6");
    await expect(summary).toContainText("1 234 567 890 123 456,78 ₽");
    await expect(summary.locator("div").filter({ has: page.getByText("НДС", { exact: true }) })).toContainText("НДС 0%");
    await expect(summary).toContainText("31.12.2027");
    await expect(row.locator(".contract-register-deadline")).toHaveText("31.12.2026");
    expect(patches).toHaveLength(1);
    expect(contract.retention_percent).toBe("22.00");
    // A manual card save must not automatically propose or confirm a budget.
    expect(mock.requests.filter(request => request.method !== "GET" && /budget-proposals/.test(request.path))).toHaveLength(0);

    await card.locator(".contract-details-summary").evaluate(element => {
      element.scrollIntoView({ block: "start" });
      const headerHeight = document.querySelector(".shell > main > header")?.getBoundingClientRect().height || 0;
      window.scrollBy(0, -headerHeight - 12);
    });
    expect(await card.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= window.innerWidth && element.scrollWidth <= element.clientWidth + 1;
    })).toBe(true);
    await info.attach(`commercial-saved-${viewport.width}`, { contentType: "image/png",
      body: await page.screenshot({ path: info.outputPath(`contract-commercial-saved-${viewport.width}.png`) }) });
    if (outputDirectory) await page.screenshot({ path: `${outputDirectory}/contract-commercial-saved-${viewport.width}.png` });

    await card.getByRole("button", { name: "Редактировать", exact: true }).click();
    await form.getByLabel("Аванс редактируемого договора с НДС, ₽").fill("1234567890123456.79");
    // Both entry points remount the same card without replacing its draft/CAS version.
    await page.getByRole("button", { name: "Схема связей", exact: true }).click();
    await expect(card).toHaveCount(1);
    await expect(form).toContainText("Версия открытого черновика: 6");
    await expect(form.getByLabel("Аванс редактируемого договора с НДС, ₽")).toHaveValue("1234567890123456.79");
    await page.getByRole("button", { name: "Закрыть договор", exact: true }).click();
    await expect(card).toHaveCount(0);
    await page.getByRole("button", { name: "К таблице", exact: true }).click();
    await row.locator(".contract-register-open").click();
    await expect(form.getByLabel("Сумма редактируемого договора с НДС, ₽")).toHaveValue(preciseAmount);
    await form.getByRole("button", { name: "Сохранить изменения" }).click();
    await expect(form.getByRole("alert")).toContainText("Аванс не может превышать сумму договора");
    await expect(form.getByRole("alert")).toContainText("synthetic-commercial-422");
    await expect(form.getByLabel("Аванс редактируемого договора с НДС, ₽")).toHaveAttribute("aria-invalid", "true");
    await expect(form.getByLabel("Аванс редактируемого договора с НДС, ₽")).toHaveValue("1234567890123456.79");
    await expect(form.getByLabel("Сумма редактируемого договора с НДС, ₽")).toHaveValue(preciseAmount);
    await expect(form.getByLabel("Ставка НДС, %", { exact: true })).toHaveValue("0.00");
    await expect(form.getByLabel("Окончание исполнения договора")).toHaveValue("2026-12-31");
    await expect(card).toContainText("Версия карточки: 6");
    expect(patches).toHaveLength(2);
    expect(await card.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= window.innerWidth && element.scrollWidth <= element.clientWidth + 1;
    })).toBe(true);

    await card.getByRole("button", { name: "Выбрать договор самому", exact: true }).click();
    const overlay = page.getByRole("dialog", { name: "Ручной выбор документа договора" });
    await expect(overlay).toBeVisible();
    const banner = page.getByRole("alert", { name: "Ошибка операции" });
    await expect(banner).toContainText("synthetic-commercial-422");
    expect(await banner.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return element.contains(hit) && rect.left >= 0 && rect.right <= window.innerWidth;
    })).toBe(true);
    await info.attach(`commercial-422-${viewport.width}`, { contentType: "image/png",
      body: await page.screenshot({ path: info.outputPath(`contract-commercial-422-${viewport.width}.png`) }) });
    if (outputDirectory) await page.screenshot({ path: `${outputDirectory}/contract-commercial-422-${viewport.width}.png` });
    await info.attach("commercial-patch-protocol", { contentType: "application/json",
      body: JSON.stringify({ fictional: true, viewport, patches }) });
    expect(mock.unexpected).toEqual([]);
  });
}
