import { expect, test } from "./storage-fixtures";

test("sent folder refresh requests Gmail sent mail and renders the imported result", async ({ page, mock }) => {
  await page.goto("/new/");
  await page.locator("aside nav").getByRole("button", { name: /Письма/ }).click();
  await page.getByRole("button", { name: /Отправленные/ }).click();
  await expect(page.getByText("Загруженных писем в этой папке нет")).toBeVisible();
  const sent = { id: 602, project_id: 2, direction: "outgoing", thread_id: "synthetic-sent",
    subject: "Синтетическое отправленное", sender: "operator@example.test", content: "Проверка без отправки.",
    headers: { to: "recipient@example.test" }, status: "completed", context_confirmed: true,
    attachments: [], drafts: [], created_at: "2026-08-01T10:00:00Z" };
  mock.hold(url => url.pathname === "/projects/2/gmail/sync").release({ body: { processed: 1, skipped: 0, failed: 0 } });
  mock.hold(url => url.pathname === "/mail/projects/2/threads" && url.searchParams.get("folder") === "sent")
    .release({ body: { threads: [{ thread_id: sent.thread_id, latest: sent }], next_cursor: null } });
  mock.hold(url => url.pathname === "/mail/projects/2/threads/synthetic-sent")
    .release({ body: { thread_id: sent.thread_id, messages: [sent] } });
  await page.getByRole("button", { name: "Получить новые" }).click();
  await expect(page.getByRole("heading", { name: sent.subject })).toBeVisible();
  const writes = mock.requests.filter(row => row.method !== "GET");
  expect(writes).toHaveLength(1);
  expect(writes[0].path).toBe("/projects/2/gmail/sync");
  expect(JSON.parse(writes[0].body!)).toEqual({ query: "in:sent", max_results: 25 });
  await expect(page.locator(".mail-folders").getByRole("button", { name: /Отправленные/ })).toHaveClass("active");
});

test("v9 keeps the production reading surface and the rail outside the content", async ({ page, mock: _mock }) => {
  await page.goto("/new/");
  await expect(page.getByRole("heading", { name: "Штаб управления проектом" })).toBeVisible();
  await expect(page.locator(".dashboard-hero")).toHaveCSS("background-color", "rgb(245, 244, 240)");
  const rail = await page.locator(".shell > aside").boundingBox();
  const main = await page.locator(".shell > main").boundingBox();
  expect(rail).not.toBeNull();
  expect(main).not.toBeNull();
  expect(rail!.x).toBe(0);
  expect(main!.x).toBeGreaterThanOrEqual(rail!.x + rail!.width - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440);
});

test("cached mail remains readable in v9 during provider outage and navigation", async ({ page, mock }) => {
  const message = { id: 501, project_id: 2, direction: "incoming", thread_id: "synthetic-501",
    subject: "Синтетический график поставки", sender: "supplier@example.test",
    content: "Поставка подтверждена. Это вымышленные данные.", summary: "Согласовать график",
    headers: { to: "operator@example.invalid" }, status: "completed", context_confirmed: true,
    attachments: [], drafts: [], created_at: "2026-09-05T10:00:00Z" };
  const folders = { provider_available: false, provider_error: "temporarily_unavailable",
    folders: [{ id: "inbox", name: "Входящие", count: 1 }, { id: "archive", name: "Архив", count: 0 }] };
  const prepare = () => {
    mock.hold(url => url.pathname === "/mail/projects/2/folders").release({ body: folders });
    mock.hold(url => url.pathname === "/mail/projects/2/threads").release({ body: {
      threads: [{ thread_id: message.thread_id, latest: message }], next_cursor: null,
    } });
    mock.hold(url => url.pathname === "/mail/projects/2/threads/synthetic-501").release({ body: {
      thread_id: message.thread_id, messages: [message],
    } });
  };
  await page.goto("/new/");
  for (let visit = 0; visit < 2; visit++) {
    prepare();
    await page.locator("aside nav").getByRole("button", { name: /Письма/ }).click();
    await expect(page.getByRole("heading", { name: message.subject })).toBeVisible();
    await expect(page.getByText(message.content, { exact: true })).toBeVisible();
    await expect(page.locator(".mail-provider-warning")).toBeVisible();
    await expect(page.getByText("Почтовый интерфейс пока недоступен")).toHaveCount(0);
    const rail = await page.locator(".shell > aside").boundingBox();
    const mail = await page.locator(".mail-client").boundingBox();
    expect(mail!.x).toBeGreaterThanOrEqual(rail!.x + rail!.width - 1);
    await page.locator("aside nav").getByRole("button", { name: "Рабочий центр", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Штаб управления проектом" })).toBeVisible();
  }
  expect(mock.requests.filter(row => row.method !== "GET")).toEqual([]);
});
