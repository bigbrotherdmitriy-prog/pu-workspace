import type { Page } from "@playwright/test";
import { expect, test, settled } from "./storage-fixtures";

// 13.10.2026, Android 17 phone: "Вкладка задачи не двигается". The Tasks list
// is laid out in the document (no inner scroll box), and the page did not
// scroll at all: `overflow-x: hidden` on html and body made <body> a scroll
// container, and its `overscroll-behavior-y: none` stopped every wheel and
// swipe from chaining up to the viewport.
const tasks = Array.from({ length: 28 }, (_, index) => ({
  id: 1000 + index, record_version: 1, title: `Синтетическая задача номер ${index + 1}`, status: "assigned", priority: "normal",
  due_date: "2026-10-20", assignee_user_id: 900, assignee_name: "Synthetic Operator", source_file_name: "Протокол.pdf",
  source_excerpt: "Цитата из протокола.", confidence: 0.8, needs_review: false, external_action_status: "proposed",
  provider_effects: { task: { status: "not_requested" }, calendar: { status: "not_requested" } },
}));
const lastTask = "#task-1027";

async function openTasks(page: Page) {
  await page.route("**/tasks?project_id=2*", route => route.fulfill({ json: { tasks } }));
  await page.goto("/new/");
  await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
  await settled(page);
  await page.locator(".android-bottom-nav").getByRole("button", { name: "Задачи", exact: true }).click();
  await page.getByRole("button", { name: "Все", exact: true }).click();
  await expect(page.locator("#task-1000")).toBeVisible();
  await expect(page.locator(lastTask)).not.toBeInViewport();
}

const scrollTop = (page: Page) => page.evaluate(() => document.scrollingElement!.scrollTop);

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("the task list scrolls with the mouse wheel", async ({ page, mock: _mock }) => {
    await openTasks(page);
    await page.mouse.move(195, 500);
    for (let step = 0; step < 20 && !(await page.locator(lastTask).isVisible() && await page.locator(lastTask).evaluate(el => el.getBoundingClientRect().bottom <= window.innerHeight)); step += 1) {
      await page.mouse.wheel(0, 800);
      await page.waitForTimeout(100);
    }
    expect(await scrollTop(page)).toBeGreaterThan(1000);
    await expect(page.locator(lastTask)).toBeInViewport();
  });

  test("the task list scrolls with a finger swipe", async ({ page, mock: _mock }) => {
    await openTasks(page);
    const cdp = await page.context().newCDPSession(page);
    // A real touch drag on the list area (finger moves up, page moves down).
    const swipe = async () => {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 195, y: 700 }] });
      for (let y = 680; y >= 160; y -= 20) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: 195, y }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await page.waitForTimeout(150);
    };
    await swipe();
    expect(await scrollTop(page), "one swipe moves the page").toBeGreaterThan(300);
    // Keep swiping until the end of the list.
    for (let step = 0; step < 40; step += 1) {
      if (await page.locator(lastTask).evaluate(el => el.getBoundingClientRect().bottom <= window.innerHeight)) break;
      await swipe();
    }
    await expect(page.locator(lastTask)).toBeInViewport();
    // Nothing intercepts touches over the list.
    const hit = await page.evaluate(() => document.elementFromPoint(195, 400)?.closest(".task-register") !== null);
    expect(hit).toBe(true);
  });
});
