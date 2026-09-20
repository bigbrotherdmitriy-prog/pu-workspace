import { expect, test } from "./storage-fixtures";

test("Ctrl+K performs scoped search with filters, pagination, exact navigation and saved views", async ({ page, mock }) => {
  mock.searchItems = [
    {
      entity_type: "task", entity_id: 901, name: "Подготовить акт", date: "2026-09-25",
      project_id: 2, contract_id: 41, counterparty: "ООО Фасад", status: "open",
      navigation: { section: "tasks", project_id: 2, entity_type: "task", entity_id: 901 },
    },
    {
      entity_type: "decision", entity_id: 902, name: "Утвердить акт", date: "2026-09-26",
      project_id: 2, contract_id: 41, counterparty: "ООО Фасад", status: "proposed",
      navigation: { section: "governance", project_id: 2, entity_type: "decision", entity_id: 902 },
    },
  ];
  await page.goto("/new/");
  await expect(page.getByLabel("Текущий проект")).toHaveValue("2");

  await page.keyboard.press("Control+K");
  const input = page.getByLabel("Поиск по проекту");
  await expect(input).toBeFocused();
  await input.fill("акт");
  await page.getByLabel("Фильтры поиска").click();
  await page.getByLabel("Задача").check();
  await expect(page.getByRole("option", { name: /Подготовить акт/ })).toBeVisible();
  await expect.poll(() => mock.requests.find(request => request.path.startsWith("/project-search?") && request.path.includes("types=task"))?.path).toContain("project_id=2");

  await page.getByRole("button", { name: "Показать ещё" }).click();
  await expect(page.getByRole("option", { name: /Утвердить акт/ })).toBeVisible();
  await page.getByRole("option", { name: /Подготовить акт/ }).click();
  await expect(page.getByRole("heading", { name: "Задачи", exact: true })).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get("search_entity_id")).toBe("901");

  await page.keyboard.press("Control+K");
  await page.getByLabel("Сохранённый вид").selectOption("601");
  await expect(input).toHaveValue("акт");
  await expect(page.getByLabel("Задача")).toBeChecked();

  const prompts = ["Новый вид", "Переименованный вид"];
  page.on("dialog", async dialog => dialog.accept(prompts.shift() || undefined));
  await page.getByTitle("Сохранить текущий запрос").click();
  await expect.poll(() => mock.count("/saved-search-views")).toBeGreaterThan(1);
  await page.getByRole("button", { name: "Переименовать" }).click();
  await expect.poll(() => mock.requests.some(request => request.method === "PATCH" && request.path === "/saved-search-views/602")).toBe(true);
  await page.getByRole("button", { name: "Удалить сохранённый вид" }).click();
  await expect.poll(() => mock.requests.some(request => request.method === "DELETE" && request.path.startsWith("/saved-search-views/602?"))).toBe(true);
});

test("project switch clears query and never shows previous project hits", async ({ page, mock }) => {
  await page.goto("/new/");
  await page.keyboard.press("Control+K");
  const input = page.getByLabel("Поиск по проекту");
  await input.fill("акт");
  await expect(page.getByRole("option", { name: /Подготовить акт/ })).toBeVisible();
  await page.getByLabel("Текущий проект").selectOption("1");
  await expect(input).toHaveValue("");
  await page.keyboard.press("Control+K");
  await expect(page.getByRole("option", { name: /Подготовить акт/ })).toHaveCount(0);
});
