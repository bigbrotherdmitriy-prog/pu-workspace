import { expect, test, settled } from "./storage-fixtures";

// Owner backlog, 14.10.2026: hundreds of auto-extracted tasks nobody touched.
// «Разобрать очередь» previews a preset, closes exactly the previewed count
// and the toast's «Отменить» restores the batch. Phone width, mocked API.
const tasks = Array.from({ length: 3 }, (_, index) => ({
  id: 3100 + index, record_version: 1, title: `Автоизвлечённая задача ${index + 1}`, status: "assigned", priority: "normal",
  due_date: "2026-09-01", assignee_user_id: 900, assignee_name: "Synthetic Operator", source_file_name: "Договор.pdf",
  source_excerpt: "Цитата из договора.", confidence: 0.4, needs_review: true, external_action_status: "proposed",
  provider_effects: { task: { status: "not_requested" }, calendar: { status: "not_requested" } },
}));

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("preview → confirm → undo of a bulk close", async ({ page, mock: _mock }) => {
    const calls: { path: string; body: Record<string, any> | null }[] = [];
    await page.route("**/tasks?project_id=2*", route => route.fulfill({ json: { tasks } }));
    await page.route("**/projects/2/tasks/bulk-close**", async route => {
      const url = new URL(route.request().url());
      const body = route.request().postData() ? JSON.parse(route.request().postData()!) : null;
      calls.push({ path: url.pathname, body });
      if (url.pathname.endsWith("/undo")) return route.fulfill({ json: { batch_id: 77, restored: 2, skipped: [] } });
      if (body?.dry_run) return route.fulfill({ json: { dry_run: true, count: 2, target_status: "cancelled",
        sample: tasks.slice(0, 2).map(({ id, title, status }) => ({ id, title, status })) } });
      return route.fulfill({ json: { dry_run: false, batch_id: 77, count: 2, target_status: "cancelled", task_ids: [3100, 3101] } });
    });

    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await page.locator(".android-bottom-nav").getByRole("button", { name: "Задачи", exact: true }).click();
    // Default filter «Мои активные» shows my overdue tasks.
    await expect(page.getByRole("button", { name: "Мои активные", exact: true })).toHaveClass(/selected/);
    await expect(page.locator("#task-3100")).toBeVisible();

    await page.getByRole("button", { name: "Разобрать очередь" }).click();
    await expect(page.getByLabel("Автоизвлечённые старше 30 дней без изменений")).toBeChecked();
    await page.getByRole("button", { name: "Показать, что попадёт" }).click();
    await expect(page.getByText("Найдено: 2")).toBeVisible();
    await expect(page.getByRole("list", { name: "Примеры" }).getByText("Автоизвлечённая задача 1")).toBeVisible();

    const confirm = page.getByRole("button", { name: "Закрыть 2 задачи (можно отменить)" });
    await confirm.scrollIntoViewIfNeeded();
    const box = await confirm.boundingBox();
    expect(box!.height, "touch target").toBeGreaterThanOrEqual(44);
    expect(box!.x + box!.width, "fits the phone width").toBeLessThanOrEqual(390);
    const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth);
    expect(overflow, "no horizontal page scroll").toBeLessThanOrEqual(0);
    await confirm.click();

    const toast = page.getByRole("status").filter({ hasText: "Закрыто: 2 задачи." });
    await expect(toast).toBeVisible();
    await expect(toast).toBeInViewport();
    await toast.getByRole("button", { name: "Отменить" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Восстановлено: 2" })).toBeVisible();

    expect(calls.map(call => call.path)).toEqual([
      "/projects/2/tasks/bulk-close", "/projects/2/tasks/bulk-close", "/projects/2/tasks/bulk-close/77/undo",
    ]);
    expect(calls[0].body).toMatchObject({ dry_run: true,
      filter: { older_than_days: 30, untouched_only: true, auto_extracted_only: true } });
    expect(calls[1].body).toMatchObject({ dry_run: false, expected_count: 2 });
  });
});
