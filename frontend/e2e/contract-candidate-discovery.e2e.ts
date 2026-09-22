import { expect, test } from "./storage-fixtures";

test("classifies 638 files in four durable batches and creates only after confirmation", async ({ page, mock }) => {
  const documents = Array.from({ length: 638 }, (_, index) => ({
    id: index + 1, name: index === 0 ? "Договор Д-1.pdf" : `Письмо ${index + 1}.pdf`,
    mime_type: "application/pdf", source: "google_drive",
  }));
  const batchSizes = [200, 200, 200, 38];
  let created = 0;
  await page.route("**/projects/2/documents", route => route.fulfill({ json: { documents } }));
  await page.route("**/projects/2/contracts", async route => {
    if (route.request().method() === "POST") {
      created += 1;
      return route.fulfill({ json: { id: 71, record_version: 1, status: "active" } });
    }
    return route.fulfill({ json: { contracts: [] } });
  });
  await page.route("**/projects/2/contracts/71/analyze", route => route.fulfill({ json: { source: {}, financial_check: {} } }));
  await page.route("**/projects/2/contracts/discovery-jobs", async route => {
    expect(route.request().method()).toBe("POST");
    return route.fulfill({ json: {
      total: 638, batch_size: 200,
      jobs: batchSizes.map((count, index) => ({ job_id: 501 + index, status: "queued", count })),
    } });
  });
  await page.route(/\/projects\/2\/contracts\/discovery-jobs\/(\d+)$/, async route => {
    const jobId = Number(route.request().url().split("/").pop());
    const batch = jobId - 501;
    const start = batch * 200 + 1;
    const ids = Array.from({ length: batchSizes[batch] }, (_, index) => start + index);
    const proposal = {
      document_id: 1, document_name: "Договор Д-1.pdf", number: "Д-1", title: "Монтаж",
      contract_kind: "customer", confidence: 0.94, evidence: ["собственный заголовок договора найден"],
      already_linked: false, parent_document_id: null, parent_contract_id: null,
    };
    const rejected = ids.filter(id => id !== 1).map(id => ({
      document_id: id, document_name: `Письмо ${id}.pdf`, number: `Письмо ${id}`,
      title: `Письмо ${id}`, contract_kind: "customer", confidence: 0.28,
      evidence: ["исключён: найдена только ссылка на другой договор"],
      reason: "исключён: найдена только ссылка на другой договор", already_linked: false,
      parent_document_id: null, parent_contract_id: null,
    }));
    return route.fulfill({ json: {
      job_id: jobId, status: "completed", progress: 100,
      result: { proposals: batch === 0 ? [proposal] : [], rejected, processed: batchSizes[batch] },
    } });
  });

  await page.goto("/new/");
  await page.getByRole("button", { name: "Договоры", exact: true }).click();
  await page.getByRole("button", { name: "Найти договоры" }).click();
  await expect(page.getByText("Найдено кандидатов: 1")).toBeVisible();
  await expect(page.getByText("Остальные документы: 637")).toBeVisible();
  expect(created).toBe(0);
  await page.getByRole("button", { name: "Проверить 1 предложений" }).click();
  expect(created).toBe(0);
  await page.getByRole("button", { name: "Создать и привязать всё дерево" }).click();
  await expect.poll(() => created).toBe(1);
  expect(mock.unexpected).toEqual([]);
});
