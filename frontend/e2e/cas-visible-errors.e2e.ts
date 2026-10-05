import { test, expect } from "./storage-fixtures";

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`CAS refusal remains visible above a real portalled overlay at ${viewport.width}`, async ({ page, mock }, info) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const contract = { id: 31, record_version: 5, number: "SYN-31", title: "Synthetic contract",
      contract_kind: "customer", status: "active", linked_documents: [], version_history: [] };
    await page.route("**/projects/2/contracts", route => route.fulfill({ contentType: "application/json",
      body: JSON.stringify({ contracts: [contract] }) }));
    await page.route("**/projects/2/contracts/31", async route => {
      mock.requests.push({ method: route.request().method(), path: "/projects/2/contracts/31", body: route.request().postData() });
      await route.fulfill({ status: 409, contentType: "application/json", headers: { "X-Request-ID": "synthetic-cas-request" },
        body: JSON.stringify({ detail: { code: "RECORD_VERSION_CONFLICT" } }) });
    });
    await page.goto("/new/");
    await expect(page.getByLabel("Текущий проект")).toHaveValue("2");
    await page.locator('aside button[title="Договоры"]').click();
    await page.locator(".contract-register-row").filter({ hasText: "SYN-31" })
      .locator(".contract-register-open").click();
    // This gate covers responsive editor/feedback layout, not drawer navigation.
    await page.setViewportSize(viewport);
    await page.getByRole("article", { name: "Карточка договора SYN-31", exact: true })
      .getByRole("button", { name: "Редактировать", exact: true }).click();
    const form = page.getByRole("form", { name: "Редактирование договора SYN-31" });
    await form.getByPlaceholder("Сумма договора, ₽").fill("12345.67");
    await form.getByRole("button", { name: "Сохранить изменения" }).click();
    await expect(form.getByRole("alert")).toContainText("synthetic-cas-request");
    await expect(form.getByPlaceholder("Сумма договора, ₽")).toHaveValue("12345.67");
    await page.getByRole("button", { name: "Выбрать договор самому", exact: true }).click();
    const overlay = page.getByRole("dialog", { name: "Ручной выбор документа договора" });
    await expect(overlay).toBeVisible();
    const banner = page.getByRole("alert", { name: "Ошибка операции" });
    await expect(banner).toContainText("synthetic-cas-request");
    expect(await banner.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return element.contains(hit) && rect.left >= 0 && rect.right <= window.innerWidth;
    })).toBe(true);
    await info.attach(`cas-overlay-${viewport.width}`, { body: await page.screenshot({ path: info.outputPath(`cas-overlay-${viewport.width}.png`) }), contentType: "image/png" });
    expect(mock.requests.filter(request => request.method === "PATCH" && request.path === "/projects/2/contracts/31")).toHaveLength(1);
    await banner.getByRole("button", { name: "Закрыть сообщение об ошибке" }).click();
    await expect(banner).toHaveCount(0);
    await expect(overlay).toBeVisible();
  });

  test(`invoice refusal preserves its real editor and stacks feedback vertically at ${viewport.width}`, async ({ page, mock }, info) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const invoice = { id: 61, project_id: 2, source_document_id: 91, amount: 125, currency: "RUB",
      selected_cost_category_id: 1, payment_purpose: "Synthetic materials", target_kind: "budget",
      status: "proposed", requires_confirmation: true, extraction_method: "llm", confidence: 0.95 };
    await page.route("**/execution/document-candidates?*", route => route.fulfill({ contentType: "application/json",
      body: JSON.stringify({ candidates: [{ document_id: 91, name: "Synthetic invoice.pdf", kind: "invoice", score: 98,
        reasons: [], hints: {}, already_linked: false, originals_changed: false }] }) }));
    await page.route("**/execution/cost-categories?*", route => route.fulfill({ contentType: "application/json",
      body: JSON.stringify({ categories: [{ id: 1, name: "Synthetic category", is_active: true }] }) }));
    await page.route("**/execution/documents/91/invoice-extraction-proposals", route => route.fulfill({
      contentType: "application/json", body: JSON.stringify(invoice) }));
    await page.route("**/execution/invoice-extraction-proposals/61", route => route.fulfill({
      contentType: "application/json", body: JSON.stringify(invoice) }));
    await page.route("**/execution/invoice-extraction-proposals/61/confirm", async route => {
      mock.requests.push({ method: route.request().method(), path: "/execution/invoice-extraction-proposals/61/confirm", body: route.request().postData() });
      await route.fulfill({ status: 422, contentType: "application/json", headers: { "X-Request-ID": "synthetic-invoice-request" },
        body: JSON.stringify({ detail: "Synthetic confirmation refusal" }) });
    });
    await page.goto("/new/");
    await expect(page.getByLabel("Текущий проект")).toHaveValue("2");
    await page.locator('aside button[title="ГПР и ДДС"]').click();
    await page.getByRole("tab", { name: "ДДС", exact: true }).click();
    await page.setViewportSize(viewport);
    await page.locator(".dds-intake-candidates > summary").click();
    await page.getByRole("button", { name: "Проверить и использовать", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "Проверка финансовых данных" });
    await expect(editor).toBeVisible();
    await editor.getByLabel("Сумма", { exact: true }).fill("321");
    await editor.getByRole("button", { name: "Подтвердить и создать предложение", exact: true }).click();
    await expect(editor.getByRole("alert")).toContainText("synthetic-invoice-request");
    await expect(editor.getByLabel("Сумма", { exact: true })).toHaveValue("321");
    expect(await editor.evaluate(element => {
      const error = element.querySelector(".form-operation-error")!.getBoundingClientRect();
      const fields = element.querySelector(".invoice-extraction-review")!.getBoundingClientRect();
      return error.bottom <= fields.top && error.width > fields.width / 2;
    })).toBe(true);
    await info.attach(`invoice-refusal-${viewport.width}`, { body: await page.screenshot({ path: info.outputPath(`invoice-refusal-${viewport.width}.png`) }), contentType: "image/png" });
    expect(mock.requests.filter(request => request.path === "/execution/invoice-extraction-proposals/61/confirm")).toHaveLength(1);
  });
}
