import type { Page } from "@playwright/test";
import { chooseThemeOnPhone, expect, test, settled } from "./storage-fixtures";

// 13.10.2026 phone review: task cards showed the evidence quote, the heuristic
// score and AI hints inline; mail folders had huge paddings and confirming a
// letter for AUTO took three steps.
const longTitle = "Подготовить и согласовать с заказчиком исполнительную документацию по монтажу навесного вентилируемого фасада секций 1–3";
const tasks = [
  {
    id: 901, record_version: 1, title: longTitle, status: "assigned", priority: "high", due_date: "2026-10-20",
    assignee_user_id: 900, assignee_name: "Synthetic Operator", source_file_name: "Протокол совещания №14.pdf",
    source_excerpt: "Подрядчику в срок до 20.10.2026 предоставить исполнительную документацию по фасаду.",
    confidence: 0.64, needs_review: true, external_action_status: "proposed", description: "Пакет ИД по фасаду",
    assignee_hint: "Петров", provider_effects: { task: { status: "not_requested" }, calendar: { status: "not_requested" } },
  },
  {
    id: 902, record_version: 1, title: "Проверить акт КС-2", status: "in_progress", priority: "normal", due_date: "2026-10-01",
    assignee_user_id: 900, assignee_name: "Synthetic Operator", source_file_name: "Письмо заказчика",
    source_excerpt: "Просим проверить акт.", confidence: 0.9, needs_review: false, external_action_status: "applied",
    provider_effects: { task: { status: "applied" }, calendar: { status: "not_requested" } },
  },
];
const letter = (overrides: Record<string, unknown> = {}) => ({
  id: 501, project_id: 2, contract_id: null, direction: "incoming", thread_id: "synthetic-501",
  subject: "Исполнительная документация по фасаду", sender: "client@example.invalid",
  content: "Добрый день! Направляем замечания к исполнительной документации.", summary: "Замечания к ИД",
  headers: { to: "operator@example.invalid" }, attachments: [], status: "ready",
  context_confirmed: false, context_version: 3, context_confidence: 0.81, context_evidence: "Упоминание фасада",
  auto_context_confirmation_state: "not_confirmed", created_at: "2026-10-13T09:00:00Z", drafts: [],
  ...overrides,
});

async function withData(page: Page) {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  let current = letter();
  await page.route("**/tasks?project_id=2*", route => route.fulfill({ json: { tasks } }));
  await page.route("**/projects/2/contracts", route => route.fulfill({ json: { contracts: [
    { id: 41, record_version: 1, number: "C-41", title: "Навесной фасад" },
    { id: 42, record_version: 1, number: "C-42", title: "Кровля" },
  ] } }));
  await page.route("**/mail/projects/2/folders", route => route.fulfill({ json: { folders: [
    { id: "inbox", name: "Входящие", count: 12 }, { id: "attention", name: "Требуют внимания", count: 3 },
    { id: "drafts", name: "Черновики", count: 1 }, { id: "sent", name: "Отправленные", count: 128 },
  ] } }));
  await page.route("**/mail/projects/2/threads?*", route => route.fulfill({ json: { threads: [{ thread_id: "synthetic-501", latest: current }], next_cursor: null } }));
  await page.route("**/mail/projects/2/threads/synthetic-501", route => route.fulfill({ json: { thread_id: "synthetic-501", messages: [current] } }));
  await page.route("**/ai-secretary/inbox/501/confirm-context", async route => {
    const body = JSON.parse(route.request().postData() || "{}");
    calls.push({ path: "confirm-context", body });
    current = letter({ contract_id: body.contract_id, context_confirmed: true, context_version: 4 });
    await route.fulfill({ json: current });
  });
  await page.route("**/ai-secretary/inbox/501/confirm-context-for-auto", async route => {
    const body = JSON.parse(route.request().postData() || "{}");
    calls.push({ path: "confirm-context-for-auto", body });
    current = letter({ contract_id: body.contract_id, context_confirmed: true, context_version: 4, auto_context_confirmation_state: "confirmed_current" });
    await route.fulfill({ json: { message_id: 501, state: "confirmed_current", already_confirmed: false } });
  });
  return calls;
}

async function noHorizontalScroll(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth), "no horizontal page scroll").toBeLessThanOrEqual(width);
}

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  for (const theme of ["Тёмная", "Светлая"]) {
    test(`task cards are compact with details on demand (${theme})`, async ({ page, mock: _mock }) => {
      await withData(page);
      await page.goto("/new/");
      await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
      await settled(page);
      await chooseThemeOnPhone(page, theme);
      await page.locator(".android-bottom-nav").getByRole("button", { name: "Задачи", exact: true }).click();
      await page.getByRole("button", { name: "Все", exact: true }).click();
      const card = page.locator("#task-901");
      await expect(card.getByText(longTitle)).toBeVisible();
      await noHorizontalScroll(page, 390);

      // Title is clamped to two lines.
      const title = card.locator(".task-body > strong");
      const lines = await title.evaluate(element => element.getBoundingClientRect().height / parseFloat(getComputedStyle(element).lineHeight));
      expect(lines).toBeLessThanOrEqual(2.05);
      const summary = card.locator(".task-mobile-summary");
      await expect(summary).toBeVisible();
      await expect(summary).toContainText("2026-10-20");
      await expect(summary).toContainText("Synthetic Operator");
      await expect(summary).toContainText("Назначена");
      for (const action of ["Изменить срок", "Поставить задачу", "В работу", "Завершить", "Подробнее"]) {
        const button = card.getByRole("button", { name: action, exact: true });
        await expect(button).toBeVisible();
        expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      // Evidence, heuristic score and AI hints stay behind "Подробнее".
      for (const hidden of [".task-source-excerpt", ".task-confidence", ".task-assignee-hint", ".task-meta"]) await expect(card.locator(hidden)).toBeHidden();
      await card.getByRole("button", { name: "Подробнее" }).click();
      await expect(card.locator(".task-source-excerpt")).toBeVisible();
      await expect(card.getByText("Эвристическая оценка 64/100")).toBeVisible();
      await expect(card.getByText("Система предположила: Петров")).toBeVisible();
      await expect(card.getByRole("combobox", { name: `Исполнитель задачи ${longTitle}` })).toBeVisible();
      await noHorizontalScroll(page, 390);
      await card.getByRole("button", { name: "Свернуть" }).click();
      await expect(card.locator(".task-source-excerpt")).toBeHidden();
    });

    test(`mail folders are compact and AUTO takes one button once the source chain is ready (${theme})`, async ({ page, mock: _mock }) => {
      const calls = await withData(page);
      await withAutoSourceChainReady(page);
      await page.goto("/new/");
      await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
      await settled(page);
      await chooseThemeOnPhone(page, theme);
      await page.locator(".android-bottom-nav").getByRole("button", { name: "Письма", exact: true }).click();
      const folders = page.getByRole("complementary", { name: "Папки почты" });
      await expect(folders.getByRole("button", { name: /Отправленные/ })).toBeVisible();
      await noHorizontalScroll(page, 390);
      for (const [name, count] of [["Входящие", "12"], ["Требуют внимания", "3"], ["Отправленные", "128"]]) {
        const row = folders.getByRole("button", { name: new RegExp(name) });
        const rowBox = (await row.boundingBox())!;
        expect(rowBox.height, `${name}: compact row`).toBeGreaterThanOrEqual(44);
        expect(rowBox.height, `${name}: compact row`).toBeLessThanOrEqual(52);
        const label = (await row.locator("span").boundingBox())!;
        const counter = (await row.locator("b", { hasText: count }).boundingBox())!;
        expect(label.x - rowBox.x, `${name}: label starts at the left`).toBeLessThan(60);
        expect(rowBox.x + rowBox.width - (counter.x + counter.width), `${name}: counter on the right`).toBeLessThan(20);
      }

      await page.getByRole("button", { name: /Исполнительная документация по фасаду/ }).click();
      await expect(page.getByRole("button", { name: "Подтвердить контекст для AUTO" })).toBeHidden();
      // The desktop link button stays hidden; the phone has its own compact one.
      await expect(page.locator("button.mail-context-manual", { hasText: /связь$/ })).toBeHidden();
      const auto = page.getByRole("button", { name: "Подтвердить для AUTO" });
      await expect(auto).toBeVisible();
      await auto.click();
      const sheet = page.getByRole("dialog", { name: "Договор письма для AUTO" });
      await expect(sheet).toBeVisible();
      await sheet.getByText("Навесной фасад").click();
      await sheet.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await expect(sheet).toBeHidden();
      await expect(page.getByText("Контекст для AUTO явно подтверждён владельцем")).toBeVisible();
      expect(calls).toEqual([
        { path: "confirm-context", body: { project_id: 2, contract_id: 41, expected_context_version: 3 } },
        { path: "confirm-context-for-auto", body: { project_id: 2, contract_id: 41, expected_context_version: 4 } },
      ]);
      await noHorizontalScroll(page, 390);
    });

    test(`AUTO for mail is deferred: the phone links a contract instead (${theme})`, async ({ page, mock: _mock }) => {
      const calls = await withData(page);
      await page.goto("/new/");
      await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
      await settled(page);
      await chooseThemeOnPhone(page, theme);
      await page.locator(".android-bottom-nav").getByRole("button", { name: "Письма", exact: true }).click();
      await page.getByRole("button", { name: /Исполнительная документация по фасаду/ }).click();
      const reading = page.getByRole("region", { name: "Просмотр переписки" });
      await expect(reading.getByRole("button", { name: /для AUTO/ })).toHaveCount(0);
      await expect(reading.getByText("AUTO для почты появится после перехода почты на основное чтение (этап V6-07)")).toBeVisible();
      const line = reading.getByTestId("mail-link-line");
      await expect(line).toContainText("Проект: Новый проект · Договор: не выбран");
      await expect(line).toContainText("нужно подтвердить");
      await expect(reading.getByText("Упоминание фасада · AI 81%")).toBeVisible();
      await reading.getByRole("button", { name: "Подтвердить связь" }).click();
      const sheet = page.getByRole("dialog", { name: "Связь письма" });
      await sheet.getByLabel("Договор").selectOption({ label: "C-41 — Навесной фасад" });
      await sheet.getByRole("button", { name: "Сохранить" }).click();
      await expect(sheet).toBeHidden();
      await expect(line).toContainText("Проект: Новый проект · Договор: C-41");
      await expect(line).toContainText("связь подтверждена");
      expect(calls).toEqual([
        { path: "confirm-context", body: { project_id: 2, contract_id: 41, expected_context_version: 3 } },
      ]);
      await noHorizontalScroll(page, 390);
    });
  }
});

/** The readiness projection once the mailbox is on primary read (V6-07 done). */
async function withAutoSourceChainReady(page: Page) {
  await page.route("**/api/v54/projects/2/autonomy-readiness", route => route.fulfill({ json: {
    project_id: 2, observed_at: "2026-10-14T07:00:00Z",
    overall: { status: "ACTIVE", blockers: [], warnings: [] },
    runtime: { state: "matching", pilot_enabled: true, producer_enabled: true,
      notification_enabled: true, scope_matches: true, component_alignment: "unverified" },
    policy: { revision: 2, hash_prefix: "synthetic", enabled: true,
      task_mode: "AUTO", notification_mode: "AUTO", external_message_mode: "CONFIRM",
      authority_epoch: 1, valid_until: "2026-10-21T07:00:00Z", ttl_seconds: 86400,
      ready: true, history: [{ revision: 2, enabled: true }] },
    authority: { state: "active", membership_role: "owner", ready: true },
    mailbox: { state: "valid_not_pilot", ready: false, valid: true, producer_ready: false,
      auto_source_chain_ready: true, auto_blocked_reason: null },
    quotas: {
      task_hourly: { used: 0, limit: 3 },
      notification_project_hourly: { used: 0, limit: 3 },
      notification_recipient_daily: { max_used: 0, limit: 10 },
    },
    operations: { actions: {}, receipts: {}, jobs: {}, incidents: [] },
  } }));
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 1000 } });
  test("keeps full task cards and the three-step mail context", async ({ page, mock: _mock }) => {
    await withData(page);
    await withAutoSourceChainReady(page);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await page.locator(".shell > aside").getByRole("button", { name: "Задачи", exact: true }).click();
    await page.getByRole("button", { name: "Все", exact: true }).click();
    const card = page.locator("#task-901");
    await expect(card.getByText("Эвристическая оценка 64/100")).toBeVisible();
    await expect(card.locator(".task-source-excerpt")).toBeVisible();
    await expect(card.locator(".task-mobile-summary")).toBeHidden();
    await expect(card.getByRole("button", { name: "Подробнее" })).toBeHidden();
    await page.locator(".shell > aside").getByRole("button", { name: "Письма", exact: true }).click();
    await page.getByRole("button", { name: /Исполнительная документация по фасаду/ }).click();
    await expect(page.locator("button.mail-context-manual", { hasText: "Подтвердить связь" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Подтвердить контекст для AUTO" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Подтвердить для AUTO" })).toBeHidden();
    await expect(page.locator(".mail-mobile-context")).toBeHidden();
    await expect(page.getByTestId("mail-link-line")).toContainText("Проект: Новый проект · Договор: не выбран");
  });

  test("hides the desktop AUTO-for-mail button while the source chain is not ready", async ({ page, mock: _mock }) => {
    await withData(page);
    await page.goto("/new/");
    await page.locator(".work-center-more > summary").click({ timeout: 2000 }).catch(() => {});
    await settled(page);
    await page.locator(".shell > aside").getByRole("button", { name: "Письма", exact: true }).click();
    await page.getByRole("button", { name: /Исполнительная документация по фасаду/ }).click();
    await expect(page.locator("button.mail-context-manual", { hasText: "Подтвердить связь" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Подтвердить контекст для AUTO" })).toHaveCount(0);
    await expect(page.getByText(/этап V6-07/)).toBeVisible();
  });
});
