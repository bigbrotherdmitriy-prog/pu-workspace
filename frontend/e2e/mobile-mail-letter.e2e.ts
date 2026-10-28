import type { CDPSession, Locator, Page } from "@playwright/test";
import { expect, test, settled } from "./storage-fixtures";

// 14.10.2026 owner reports from a 390 px phone, letter view after #169/#171:
// 1. "Вниз не прокручивается" — a long letter could not be scrolled, the end
//    of the body and the attachments were unreachable;
// 2. the letter showed «Контекст не определён» although it was linked, and
//    there was no way to link it to a contract from the phone.
const longBody = Array.from({ length: 80 }, (_, index) => `Строка ${index + 1} длинного письма поставщика о сроках и объёмах поставки.`).join("\n")
  + "\nКОНЕЦ ПИСЬМА";
const attachments = [1, 2, 3].map(index => ({
  attachment_id: `att-${index}`, name: `Вложение-${index}.pdf`, mime_type: "application/pdf", size: 4096 * index,
}));

type Raw = Record<string, unknown> & { id: number; thread_id: string };
const letter = (id: number, extra: Record<string, unknown> = {}): Raw => ({
  id, project_id: 2, contract_id: null, direction: "incoming", thread_id: `synthetic-${id}`,
  subject: `Письмо поставщика №${id}`, sender: `supplier${id}@example.invalid`,
  content: `Текст письма №${id}.`, summary: `Сводка №${id}`,
  headers: { to: "operator@example.invalid" }, attachments: [], status: "ready",
  context_confirmed: false, context_version: 1, context_evidence: "",
  auto_context_confirmation_state: "not_confirmed", created_at: "2026-10-13T09:00:00Z", drafts: [],
  ...extra,
});

async function withMail(page: Page, letters: Raw[]) {
  await page.route("**/projects/2/contracts", route => route.fulfill({ json: { contracts: [
    { id: 71, project_id: 2, number: "Д-71", title: "Фасад", status: "active", record_version: 1 },
    { id: 72, project_id: 2, number: "Д-72", title: "Кровля", status: "active", record_version: 1 },
  ] } }));
  await page.route("**/mail/projects/2/threads?*", route => route.fulfill({ json: {
    threads: letters.map(item => ({ thread_id: item.thread_id, message_count: 1, latest: item })), next_cursor: null,
  } }));
  await page.route("**/mail/projects/2/threads/*", route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() || "");
    const found = letters.find(item => item.thread_id === id) || letters[0];
    return route.fulfill({ json: { thread_id: found.thread_id, messages: [found] } });
  });
  await page.route("**/ai-secretary/inbox/*/confirm-context", async route => {
    const id = Number(new URL(route.request().url()).pathname.split("/").at(-2));
    const body = route.request().postDataJSON() as { project_id: number; contract_id: number | null; expected_context_version: number };
    const index = letters.findIndex(item => item.id === id);
    if (letters[index].context_version !== body.expected_context_version) {
      return route.fulfill({ status: 409, json: { detail: "context_version_conflict" } });
    }
    letters[index] = { ...letters[index], project_id: body.project_id, contract_id: body.contract_id,
      context_confirmed: true, context_version: Number(letters[index].context_version) + 1 };
    return route.fulfill({ json: letters[index] });
  });
}

async function openMail(page: Page) {
  await page.goto("/new/");
  await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
  await settled(page);
  await page.locator(".android-bottom-nav").getByRole("button", { name: "Письма", exact: true }).click();
}

/** The element's last line is in the viewport and not under the bottom nav /
 * floating buttons. */
async function reachable(_page: Page, target: Locator) {
  return target.evaluate((element) => {
    const box = element.getBoundingClientRect();
    if (box.height === 0 || box.bottom > window.innerHeight || box.bottom < 16) return false;
    const y = box.bottom - Math.min(box.height / 2, 8);
    // Both ends of the line: the floating PU / + buttons sit on the right.
    return [box.left + 8, box.right - 8].every((edge) => {
      const hit = document.elementFromPoint(Math.max(0, Math.min(edge, window.innerWidth - 1)), y);
      return Boolean(hit && (element === hit || element.contains(hit)));
    });
  });
}

async function wheelUntil(page: Page, target: Locator) {
  await page.mouse.move(195, 420);
  for (let step = 0; step < 40 && !(await reachable(page, target)); step += 1) {
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(80);
  }
  return reachable(page, target);
}

async function swipeUntil(page: Page, cdp: CDPSession, target: Locator) {
  for (let step = 0; step < 60 && !(await reachable(page, target)); step += 1) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 195, y: 640 }] });
    for (let y = 620; y >= 200; y -= 20) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: 195, y }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(150);
  }
  return reachable(page, target);
}

const longLetter = () => letter(700, {
  subject: "Длинное письмо поставщика", content: longBody, attachments,
  project_id: 2, contract_id: 71, context_confirmed: true,
});
const thirtyThreads = () => [longLetter(), ...Array.from({ length: 29 }, (_, index) => letter(699 - index))];

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  for (const mode of ["wheel", "touch"] as const) {
    test(`a long letter scrolls to its end and last attachment (${mode})`, async ({ page, mock: _mock }) => {
      await withMail(page, thirtyThreads());
      await openMail(page);
      const reading = page.getByRole("region", { name: "Просмотр переписки" });
      await page.getByRole("region", { name: "Список писем" }).getByRole("button", { name: /Длинное письмо поставщика/ }).click();
      await expect(reading.getByRole("heading", { name: "Длинное письмо поставщика" })).toBeVisible();
      // The body is one <pre>; its bottom edge is the "КОНЕЦ ПИСЬМА" line.
      const end = reading.locator("pre", { hasText: "КОНЕЦ ПИСЬМА" });
      const lastAttachment = reading.getByText("Вложение-3.pdf");
      await expect(lastAttachment).toBeAttached();
      expect(await reachable(page, lastAttachment), "the last attachment starts below the first screen").toBe(false);
      const cdp = mode === "touch" ? await page.context().newCDPSession(page) : null;
      const scrollTo = (target: Locator) => cdp ? swipeUntil(page, cdp, target) : wheelUntil(page, target);
      expect(await scrollTo(end), "end of the letter body is reachable").toBe(true);
      expect(await scrollTo(lastAttachment), "last attachment is reachable, not under the bottom nav").toBe(true);
    });

    test(`the thread list with 30 letters scrolls to the last one (${mode})`, async ({ page, mock: _mock }) => {
      await withMail(page, thirtyThreads());
      await openMail(page);
      const list = page.getByRole("region", { name: "Список писем" });
      const last = list.getByRole("button", { name: /Письмо поставщика №671/ });
      await expect(list.getByRole("button", { name: /Длинное письмо поставщика/ })).toBeVisible();
      expect(await reachable(page, last)).toBe(false);
      const cdp = mode === "touch" ? await page.context().newCDPSession(page) : null;
      expect(await (cdp ? swipeUntil(page, cdp, last) : wheelUntil(page, last)), "last thread is reachable").toBe(true);
    });
  }

  test("the letter shows its real link and the contract can be changed from the phone", async ({ page, mock: _mock }) => {
    await withMail(page, [letter(800, { contract_id: 71, context_confirmed: false, context_version: 3 })]);
    await openMail(page);
    await page.getByRole("region", { name: "Список писем" }).getByRole("button", { name: /Письмо поставщика №800/ }).click();
    const reading = page.getByRole("region", { name: "Просмотр переписки" });
    const line = reading.getByTestId("mail-link-line");
    await expect(line).toBeVisible();
    await expect(line).toContainText("Проект: Новый проект · Договор: Д-71");
    await expect(line).toContainText("нужно подтвердить");
    await expect(reading.getByText("Контекст не определён")).toHaveCount(0);
    // AUTO for mail is deferred (ADR-V6-07): no AUTO button, an explanation instead.
    await expect(reading.getByRole("button", { name: /для AUTO/ })).toHaveCount(0);
    await expect(reading.getByText(/этап V6-07/)).toBeVisible();

    const change = reading.getByRole("button", { name: "Подтвердить связь" });
    await expect(change).toBeVisible();
    expect((await change.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await change.click();
    const sheet = page.getByRole("dialog", { name: "Связь письма" });
    await expect(sheet).toBeVisible();
    await sheet.getByLabel("Договор").selectOption({ label: "Д-72 — Кровля" });
    await sheet.getByRole("button", { name: "Сохранить" }).click();
    await expect(sheet).toBeHidden();
    await expect(line).toContainText("Проект: Новый проект · Договор: Д-72");
    await expect(line).toContainText("связь подтверждена");
    await expect(reading.getByRole("button", { name: "Изменить связь" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});
