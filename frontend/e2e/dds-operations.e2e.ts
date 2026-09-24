import { expect, test } from "./storage-fixtures";

test("cancels an unlinked DDS proposal with confirmation and persists after reload", async ({ page, mock }) => {
  const row = { id: 9, direction: "outflow", title: "Тестовый счёт", planned_date: "2026-09-21", planned_amount: 1234.56, actual_amount: 0, currency: "RUB", status: "proposed", source_document_id: 90, record_version: 1 };
  let mutations = 0;
  await page.route("**/execution/overview?*", route => route.fulfill({ json: {
    cash_flow: [row], budget: [], baselines: [], schedule: [], acts: [], procurement: [], summary: {},
  } }));
  await page.route("**/execution/cash-flow/9/status", async route => {
    expect(route.request().method()).toBe("PATCH");
    expect(route.request().postDataJSON()).toEqual({ status: "cancelled" });
    row.status = "cancelled"; mutations += 1;
    await route.fulfill({ json: { id: 9, status: row.status } });
  });
  await page.goto("/new/");
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "Детализация", exact: true }).click();
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "Отменить операцию Тестовый счёт" }).click();
  expect(mutations).toBe(0);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Отменить операцию Тестовый счёт" }).click();
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  expect(mutations).toBe(1);
  await expect(page.getByRole("button", { name: "Отменить операцию Тестовый счёт" })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "Детализация", exact: true }).click();
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  expect(mock.unexpected).toEqual([]);
});

test("uploads several dropped invoices and opens the chosen document for human review", async ({ page, mock }) => {
  const uploads: string[] = [];
  const proposals: number[] = [];
  await page.route("**/local-upload/analyze", async route => {
    const body = route.request().postDataJSON();
    uploads.push(body.files[0].path);
    await route.fulfill({ json: { jobs: [{ job_id: uploads.length, status: "queued" }] } });
  });
  await page.route("**/local-upload/projects/*/jobs/*", route => route.fulfill({ json: {
    job_id: Number(route.request().url().split("/").pop()), status: "completed", progress: 100,
    result: { processed: 1, skipped: 0, tasks: 0, risks: 0, decisions: 0, drafts: 0, documents: [600 + Number(route.request().url().split("/").pop())] },
  } }));
  await page.route("**/execution/document-candidates?*", route => route.fulfill({ json: { candidates: uploads.map((name, index) => ({
    document_id: 601 + index, name, kind: "invoice", score: 95, hints: {}, reasons: [], already_linked: false,
  })) } }));
  await page.route("**/execution/documents/*/invoice-extraction-proposals", async route => {
    const id = Number(route.request().url().split("/").at(-2)); proposals.push(id);
    await route.fulfill({ json: { id: 501, project_id: 2, source_document_id: id, amount: 100, currency: "RUB", status: "proposed", target_kind: "cash_flow", extraction_method: "regex", confidence: 0.35, requires_confirmation: true } });
  });
  await page.goto("/new/");
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  await expect(page.getByRole("button", { name: "Добавить счета из папки" })).toBeVisible();
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["synthetic PDF"], "first.pdf", { type: "application/pdf" }));
    transfer.items.add(new File(["synthetic PDF"], "second.pdf", { type: "application/pdf" }));
    return transfer;
  });
  await page.locator(".dds-invoice-drop").dispatchEvent("drop", { dataTransfer: data });
  expect(uploads).toEqual([]);
  await page.getByRole("button", { name: "Загрузить и разобрать (2)" }).click();
  await expect(page.getByRole("list", { name: "Результаты загрузки счетов" }).getByRole("button", { name: "Проверить счёт" })).toHaveCount(2);
  expect(uploads).toEqual(["first.pdf", "second.pdf"]);
  expect(proposals).toEqual([]);
  await page.getByRole("list", { name: "Результаты загрузки счетов" }).getByRole("button", { name: "Проверить счёт" }).nth(1).click();
  await expect(page.locator("#invoice-extraction-review")).toBeVisible();
  expect(proposals).toEqual([602]);
  expect(mock.unexpected).toEqual([]);
});
