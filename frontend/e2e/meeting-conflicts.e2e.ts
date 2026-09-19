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
  await expect(page.getByText("Встреча создана. Проверьте время с участниками.")).toBeVisible();
  expect(mock.meetings).toHaveLength(2);
});
