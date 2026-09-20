import { expect, test } from "./storage-fixtures";

test("warns about an overlapping participant without blocking either meeting", async ({ page, mock }) => {
  await page.goto("/new/");
  await page.getByRole("button", { name: "Совещания" }).click();

  const title = page.getByPlaceholder("Название совещания");
  const scheduledAt = page.locator('input[type="datetime-local"]');
  const participants = page.getByLabel("Участники проекта");

  await title.fill("Первое совещание");
  await scheduledAt.fill("2026-09-26T10:00");
  await participants.selectOption("900");
  await page.getByRole("button", { name: "Запланировать" }).click();
  await expect(page.getByRole("heading", { name: "Первое совещание" })).toBeVisible();

  await title.fill("Пересекающееся совещание");
  await scheduledAt.fill("2026-09-26T10:30");
  await participants.selectOption("900");
  await page.getByRole("button", { name: "Запланировать" }).click();

  await expect(page.getByRole("heading", { name: "Пересекающееся совещание" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Конфликт времени: 1");
  await expect(page.getByRole("alert")).toContainText("Первое совещание");
  await expect(page.getByText("Встреча создана. Проверьте время и занятость.")).toBeVisible();
  expect(mock.meetings).toHaveLength(2);
});

test("warns about the same organization resource without blocking either meeting", async ({ page, mock }) => {
  await page.goto("/new/");
  await page.getByRole("button", { name: "Совещания" }).click();

  await page.getByPlaceholder("Название ресурса").fill("Переговорная Север");
  await page.getByLabel("Вместимость ресурса").fill("8");
  await page.getByRole("button", { name: "Добавить ресурс" }).click();
  const resources = page.getByLabel("Помещения и ресурсы");
  await expect(resources.getByRole("option", { name: /Переговорная Север/ })).toBeVisible();

  const title = page.getByPlaceholder("Название совещания");
  const scheduledAt = page.locator('input[type="datetime-local"]');
  await title.fill("Бронь комнаты 1");
  await scheduledAt.fill("2026-09-26T10:00");
  await resources.selectOption("501");
  await page.getByRole("button", { name: "Запланировать" }).click();

  await title.fill("Бронь комнаты 2");
  await scheduledAt.fill("2026-09-26T10:30");
  await resources.selectOption("501");
  await page.getByRole("button", { name: "Запланировать" }).click();

  await expect(page.getByRole("alert")).toContainText("Конфликт времени: 1");
  await expect(page.getByRole("alert")).toContainText("ресурсы: Переговорная Север");
  expect(mock.resources).toHaveLength(1);
  expect(mock.meetings).toHaveLength(2);
});
