import { expect, test } from "./storage-fixtures";

test("working center renders the structural engineering control room", async ({ page, mock: _mock }, testInfo) => {
  await page.goto("/new/");

  const deck = page.locator(".dashboard-overview-deck");
  await expect(deck).toBeVisible();
  await expect(deck.getByRole("heading", { name: "Штаб управления проектом" })).toBeVisible();
  await expect(deck.locator(".dashboard-focus")).toContainText("Контур стабилен");
  await expect(deck.locator(".dashboard-metrics")).toBeVisible();
  await expect(page.locator("aside .nav-group")).toHaveCount(4);

  await testInfo.attach("v9-structural-control-room", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
});
