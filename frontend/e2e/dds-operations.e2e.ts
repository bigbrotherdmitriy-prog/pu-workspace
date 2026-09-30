import { expect, test } from "./storage-fixtures";

test("combines repeated DDS cost names and distributes their sums by month", async ({ page, mock }) => {
  await page.route("**/execution/overview?*", route => route.fulfill({ json: {
    cash_flow: [
      { id: 31, direction: "outflow", title: "ЭМ Щиты", planned_date: "2026-02-10", planned_amount: 400, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1, object_name: "Общие", category: "Оборудование" },
      { id: 32, direction: "outflow", title: " эм   щиты ", planned_date: "2026-02-18", planned_amount: 100, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1, object_name: "Общие", category: "Оборудование" },
      { id: 33, direction: "outflow", title: "ЭМ Щиты", planned_date: "2026-03-05", planned_amount: 600, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1, object_name: "Общие", category: "Оборудование" },
    ], budget: [], baselines: [], schedule: [], acts: [], procurement: [], summary: {},
  } }));

  await page.goto("/new/");
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();

  const costRows = page.locator("tr.operation", { has: page.getByText("ЭМ Щиты", { exact: true }) });
  await expect(costRows).toHaveCount(1);
  await expect(costRows).toContainText("1 100,00 ₽");
  await expect(costRows.getByTitle("Сумма 2 операций")).toContainText("500,00 ₽");
  await expect(costRows.getByLabel("План ЭМ Щиты 2026-03")).toHaveValue("600");
  expect(mock.unexpected).toEqual([]);
});

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
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "Удалить операцию Тестовый счёт" }).click();
  expect(mutations).toBe(0);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Удалить операцию Тестовый счёт" }).click();
  await expect(page.getByText("Тестовый счёт", { exact: true })).toHaveCount(0);
  expect(mutations).toBe(1);
  await expect(page.getByRole("button", { name: "Удалить операцию Тестовый счёт" })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "Детализация", exact: true }).click();
  await expect(page.getByText("Тестовый счёт", { exact: true })).toHaveCount(0);
  await page.getByLabel("Фильтр по статусу").selectOption("cancelled");
  await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  expect(mock.unexpected).toEqual([]);
});

test("moves an aggregated DDS cell to another month with the visible handle", async ({ page, mock }) => {
  const rows = [
    { id: 31, direction: "outflow", title: "ЭМ Щиты", planned_date: "2026-02-10", planned_amount: 400, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1, object_name: "Общие", category: "Оборудование" },
    { id: 32, direction: "outflow", title: "эм щиты", planned_date: "2026-02-18", planned_amount: 100, actual_amount: 0, currency: "RUB", status: "proposed", record_version: 1, object_name: "Общие", category: "Оборудование" },
  ];
  const mutations: Array<{ id: number; body: Record<string, unknown> }> = [];
  await page.route("**/execution/overview?*", route => route.fulfill({ json: {
    cash_flow: rows, budget: [], baselines: [], schedule: [], acts: [], procurement: [], summary: {},
  } }));
  await page.route("**/execution/cash-flow/*/plan-mutations", async route => {
    const id = Number(route.request().url().split("/").at(-2));
    mutations.push({ id, body: route.request().postDataJSON() });
    await route.fulfill({ json: { mutation_id: 100 + id, result_id: id, record_version: 2 } });
  });

  await page.goto("/new/");
  await page.getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
  await page.getByRole("tab", { name: "ДДС", exact: true }).click();
  const row = page.locator("tr.operation", { has: page.getByText("ЭМ Щиты", { exact: true }) });
  await page.getByRole("button", { name: "Перенести ЭМ Щиты, 2 операций из 2026-02" }).dragTo(row.locator("td").nth(4));
  await expect(page.getByRole("dialog", { name: "Действие с плановой суммой" })).toBeVisible();
  await page.getByRole("button", { name: "Переместить", exact: true }).click();

  await expect.poll(() => mutations.length).toBe(2);
  expect(mutations.map(item => [item.id, item.body.operation, item.body.planned_date])).toEqual([
    [31, "move", "2026-03-10"],
    [32, "move", "2026-03-18"],
  ]);
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
