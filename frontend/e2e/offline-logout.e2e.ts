import { expect, test } from "./storage-fixtures";

test("offline logout closes the mounted App and preserves a durable revocation", async ({ page, context, mock }) => {
  await page.goto("/new/");
  await expect(page.locator(".shell")).toBeVisible();
  await page.route("**/auth/logout/revoke", route => route.abort("internetdisconnected"));
  await page.route(url => url.origin === "http://127.0.0.1:4179" && url.pathname === "/health", route => route.abort("internetdisconnected"));
  await context.setOffline(true);
  let warning = "";
  page.once("dialog", async dialog => { warning = dialog.message(); await dialog.accept(); });
  await page.getByRole("button", { name: "Выйти из аккаунта", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Вход в PU Workspace" })).toBeVisible();
  await expect(page.locator(".shell")).toHaveCount(0);
  expect(warning).toContain("Неотправленные фото и правки");
  expect(warning).toContain("Несохранённые поля форм");
  expect(await page.evaluate(() => localStorage.getItem("pu.local-signout.v1"))).toBe("1");
  expect(await page.evaluate(() => localStorage.getItem("pu.logout-revoke.v1.900"))).toBe("900." + "a".repeat(64));
  expect((await context.cookies()).some(cookie => cookie.name === "pu_csrf")).toBe(false);
});

test("reload with an old HttpOnly cookie never restores the mounted private App", async ({ page, context, mock }) => {
  await context.addCookies([{ name: "pu_session", value: "SYNTHETIC-OLD-COOKIE", url: "http://127.0.0.1:4179", httpOnly: true }]);
  await page.route("**/auth/logout/revoke", route => route.fulfill({ json: { status: "revoked" } }));
  await page.goto("/new/");
  await expect(page.locator(".shell")).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Выйти из аккаунта", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Вход в PU Workspace" })).toBeVisible();
  const oldBootstrapCalls = mock.count("/auth/me");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Вход в PU Workspace" })).toBeVisible();
  await expect(page.locator(".shell")).toHaveCount(0);
  expect(mock.count("/auth/me")).toBe(oldBootstrapCalls);
  expect((await context.cookies()).some(cookie => cookie.name === "pu_session")).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem("pu.local-signout.v1"))).toBe("1");
});
