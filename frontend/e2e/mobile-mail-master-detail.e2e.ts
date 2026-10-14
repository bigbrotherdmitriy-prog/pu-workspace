import type { Page } from "@playwright/test";
import { chooseThemeOnPhone, expect, test, settled } from "./storage-fixtures";

// 13.10.2026 phone review: the mail screen stacked the folder list, the thread
// list and the opened letter on one long page. Phones now get master-detail:
// the list first, the letter on its own screen with "Назад".
const letter = (id: number) => ({
  id, project_id: 2, contract_id: null, direction: "incoming", thread_id: `synthetic-${id}`,
  subject: `Письмо поставщика №${id}`, sender: `supplier${id}@example.invalid`,
  content: `Текст письма №${id}. Просим подтвердить сроки поставки.`, summary: `Сводка №${id}`,
  headers: { to: "operator@example.invalid" }, attachments: [], status: "ready",
  context_confirmed: false, context_version: 1, context_confidence: 0.5, context_evidence: "Синтетика",
  auto_context_confirmation_state: "not_confirmed", created_at: "2026-10-13T09:00:00Z", drafts: [],
});
const letters = Array.from({ length: 30 }, (_, index) => letter(600 - index));

async function withMail(page: Page) {
  await page.route("**/mail/projects/2/folders", route => route.fulfill({ json: { folders: [
    { id: "inbox", name: "Входящие", count: 30 }, { id: "attention", name: "Требуют внимания", count: 3 },
    { id: "drafts", name: "Черновики", count: 1 }, { id: "sent", name: "Отправленные", count: 128 },
    { id: "archive", name: "Архив", count: 4 }, { id: "spam", name: "Спам", count: 0 },
    { id: "trash", name: "Удалённые", count: 0 }, { id: "all", name: "Вся почта", count: 160 },
  ] } }));
  await page.route("**/mail/projects/2/threads?*", route => route.fulfill({ json: {
    threads: letters.map(item => ({ thread_id: item.thread_id, message_count: 1, latest: item })), next_cursor: null,
  } }));
  await page.route("**/mail/projects/2/threads/*", route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() || "");
    const found = letters.find(item => item.thread_id === id) || letters[0];
    return route.fulfill({ json: { thread_id: found.thread_id, messages: [found] } });
  });
}

async function noHorizontalScroll(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth), "no horizontal page scroll").toBeLessThanOrEqual(width);
}

// Every element that is scrolled, so the check does not depend on which
// container the mobile shell happens to scroll.
async function scrollOffsets(page: Page) {
  return page.evaluate(() => [document.scrollingElement!, ...document.querySelectorAll("body *")]
    .filter(element => element.scrollTop > 0)
    .map(element => `${element.tagName.toLowerCase()}.${String(element.className).trim().split(/\s+/).join(".")}=${Math.round(element.scrollTop)}`));
}

async function scrolledBy(page: Page) {
  return page.evaluate(() => Math.max(0, ...[document.scrollingElement!, ...document.querySelectorAll("body *")].map(element => element.scrollTop)));
}

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  for (const theme of ["Тёмная", "Светлая"]) {
    test(`mail is a list first, then one letter with "Назад" (${theme})`, async ({ page, mock: _mock }) => {
      await withMail(page);
      await page.goto("/new/");
      await settled(page);
      await chooseThemeOnPhone(page, theme);
      await page.locator(".android-bottom-nav").getByRole("button", { name: "Письма", exact: true }).click();

      const list = page.getByRole("region", { name: "Список писем" });
      const reading = page.getByRole("region", { name: "Просмотр переписки" });
      const folders = page.getByRole("complementary", { name: "Папки почты" });
      await expect(list.getByRole("button", { name: /Письмо поставщика №600/ })).toBeVisible();
      await expect(reading).toBeHidden();
      await noHorizontalScroll(page, 390);

      // Folders are one horizontal chip row, not a tall vertical list.
      const nav = folders.locator("nav");
      const navBox = (await nav.boundingBox())!;
      expect(navBox.height, "folder chips take one row").toBeLessThanOrEqual(56);
      const inbox = (await nav.getByRole("button", { name: /Входящие/ }).boundingBox())!;
      const sent = (await nav.getByRole("button", { name: /Отправленные/ }).boundingBox())!;
      expect(Math.abs(inbox.y - sent.y), "chips share a row").toBeLessThan(2);
      expect(await nav.evaluate(element => element.scrollWidth > element.clientWidth), "chip row scrolls sideways").toBe(true);

      // Scroll the list down, open a letter near the bottom.
      const target = list.getByRole("button", { name: /Письмо поставщика №575/ });
      await target.scrollIntoViewIfNeeded();
      const before = await scrollOffsets(page);
      expect(await scrolledBy(page), `the list was scrolled: ${before.join(", ")}`).toBeGreaterThan(200);
      await target.click();

      await expect(reading).toBeVisible();
      await expect(reading.getByRole("heading", { name: "Письмо поставщика №575" })).toBeVisible();
      await expect(list).toBeHidden();
      await expect(folders).toBeHidden();
      const back = reading.getByRole("button", { name: "Назад" });
      await expect(back).toBeVisible();
      expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect((await back.boundingBox())!.y, "back button is at the top of the screen").toBeLessThan(150);
      // The letter can be linked from the phone; the AUTO shortcut stays hidden
      // until the mail source chain is ready (ADR-V6-07, AUTO for mail deferred).
      await expect(reading.getByRole("button", { name: "Подтвердить связь" })).toBeVisible();
      await expect(reading.getByRole("button", { name: "Подтвердить для AUTO" })).toHaveCount(0);
      await noHorizontalScroll(page, 390);

      await back.click();
      await expect(list).toBeVisible();
      await expect(reading).toBeHidden();
      await expect(target).toBeInViewport();
      expect(await scrollOffsets(page), "list scroll position is preserved").toEqual(before);
      await noHorizontalScroll(page, 390);
    });
  }
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });
  test("keeps the list and the letter side by side", async ({ page, mock: _mock }) => {
    await withMail(page);
    await page.goto("/new/");
    await settled(page);
    await page.locator(".shell > aside").getByRole("button", { name: "Письма", exact: true }).click();
    const list = page.getByRole("region", { name: "Список писем" });
    const reading = page.getByRole("region", { name: "Просмотр переписки" });
    await list.getByRole("button", { name: /Письмо поставщика №598/ }).click();
    await expect(reading.getByRole("heading", { name: "Письмо поставщика №598" })).toBeVisible();
    await expect(list).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Папки почты" })).toBeVisible();
    await expect(reading.getByRole("button", { name: "Назад" })).toBeHidden();
    const listBox = (await list.boundingBox())!;
    const readingBox = (await reading.boundingBox())!;
    expect(readingBox.x, "letter is to the right of the list").toBeGreaterThanOrEqual(listBox.x + listBox.width - 1);
    expect(Math.abs(readingBox.y - listBox.y), "same row").toBeLessThan(2);
  });
});
