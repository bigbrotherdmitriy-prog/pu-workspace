import { chromium } from "@playwright/test";

const required = ["PUW_LIVE_BASE_URL", "PUW_LIVE_EMAIL", "PUW_LIVE_PASSWORD", "PUW_LIVE_PROJECT_ID", "PUW_LIVE_USER_ID"];
for (const name of required) {
  if (!process.env[name]) throw new Error(`Missing ${name}`);
}

const baseURL = process.env.PUW_LIVE_BASE_URL;
const projectId = Number(process.env.PUW_LIVE_PROJECT_ID);
const userId = String(process.env.PUW_LIVE_USER_ID);
const marker = `V3-04a live ${Date.now()}`;
const firstTitle = `${marker} first`;
const secondTitle = `${marker} overlap`;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();

try {
  await page.goto(`${baseURL}/new/`);
  await page.getByLabel("Email").fill(process.env.PUW_LIVE_EMAIL);
  await page.getByLabel("Пароль").fill(process.env.PUW_LIVE_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  const project = page.getByRole("combobox").first();
  await project.waitFor();
  if (await project.inputValue() !== String(projectId)) {
    const membersLoaded = page.waitForResponse(response => response.url().includes(`/projects/${projectId}/members`));
    await project.selectOption(String(projectId));
    await membersLoaded;
  }
  await page.getByRole("button", { name: "Совещания", exact: true }).click();
  await page.getByRole("heading", { name: "Новое совещание" }).waitFor();

  const title = page.getByPlaceholder("Название совещания");
  const date = page.locator('input[type="datetime-local"]');
  const participants = page.getByLabel("Участники проекта");
  await participants.selectOption(userId);

  await title.fill(firstTitle);
  await date.fill("2026-09-26T10:00");
  const firstResponse = page.waitForResponse(response => response.url().endsWith("/management/meetings") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Запланировать" }).click();
  const first = await (await firstResponse).json();

  await title.fill(secondTitle);
  await date.fill("2026-09-26T10:30");
  await participants.selectOption(userId);
  const secondResponse = page.waitForResponse(response => response.url().endsWith("/management/meetings") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Запланировать" }).click();
  const second = await (await secondResponse).json();

  if (first.id === second.id || second.has_conflicts !== true || second.conflict_count !== 1) {
    throw new Error(`Unexpected API conflict projection: ${JSON.stringify({ first, second })}`);
  }
  await page.getByRole("alert").filter({ hasText: firstTitle }).waitFor();
  const list = await page.evaluate(async id => {
    const response = await fetch(`/management/meetings?project_id=${id}&limit=200`);
    if (!response.ok) throw new Error(`Meeting list failed: ${response.status}`);
    return response.json();
  }, projectId);
  const persisted = list.meetings.filter(item => [first.id, second.id].includes(item.id));
  if (persisted.length !== 2 || !persisted.every(item => item.has_conflicts)) {
    throw new Error(`Both persisted meetings must expose the conflict: ${JSON.stringify(persisted)}`);
  }
  if (process.env.PUW_LIVE_SCREENSHOT) await page.screenshot({ path: process.env.PUW_LIVE_SCREENSHOT, fullPage: true });
  console.log(JSON.stringify({ status: "PASS", project_id: projectId, meeting_ids: [first.id, second.id], conflict_count: second.conflict_count }));
} catch (error) {
  if (process.env.PUW_LIVE_SCREENSHOT) await page.screenshot({ path: process.env.PUW_LIVE_SCREENSHOT, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
