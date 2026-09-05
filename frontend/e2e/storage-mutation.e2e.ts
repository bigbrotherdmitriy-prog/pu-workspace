import type { Page } from "@playwright/test";
import { test, expect, start, settled, release, type StorageApi } from "./storage-fixtures";

const proposal = { id: 81, folder_name: "Synthetic snapshot", status: "approved", copy_folder_id: "virtual:31",
  originals_modified: false, actions: [{ id: 82, source: "old.pdf", proposed_name: "standard.pdf",
    target_folder: "contracts", user_decision: "approved", confidence: 1, reasoning: "Synthetic exact fixture" }] };

function preview(recordVersion = 1, canRollback = false) {
  return { project_id: 2, proposal_id: 81, action_id: 82, record_version: recordVersion, kind: "rename",
    before_name: "old.pdf", after_name: "standard.pdf", provider: "google_drive", synthetic_only: true,
    execution_allowed: true, can_rollback: canRollback };
}

async function openProposal(page: Page, mock: StorageApi) {
  mock.reply("GET", "/organizer/proposals?project_id=2", { body: { proposals: [proposal] } });
  await start(page);
  await page.getByRole("button", { name: "Предложения", exact: true }).click();
}

test("storage mutation confirm, measured progress, receipt and explicit rollback", async ({ page, mock }) => {
  let revision = 1;
  let statusReads = 0;
  mock.reply("GET", "/projects/2/storage-mutations/81/actions/82/prepare", () => ({ body: preview(revision, revision > 1) }));
  mock.reply("POST", "/projects/2/storage-mutations/confirm", { body: { job_id: 71, project_id: 2, status: "queued", already_queued: false, record_version: 1 } });
  mock.reply("GET", "/projects/2/storage-mutations/jobs/71", () => {
    statusReads += 1;
    if (statusReads === 1) return { body: { job_id: 71, project_id: 2, status: "running", progress: 42, outcome: null, record_version: null } };
    revision = 2;
    return { body: { job_id: 71, project_id: 2, status: "completed", progress: 100, outcome: "applied", record_version: 2 } };
  });
  mock.reply("POST", "/projects/2/storage-mutations/rollback", { body: { job_id: 72, project_id: 2, status: "queued", already_queued: false, record_version: 2 } });
  mock.reply("GET", "/projects/2/storage-mutations/jobs/72", () => {
    revision = 3;
    return { body: { job_id: 72, project_id: 2, status: "completed", progress: 100, outcome: "rolled_back", record_version: 3 } };
  });
  await openProposal(page, mock);
  await expect(page.getByText("old.pdf → standard.pdf")).toBeVisible();
  await page.getByRole("button", { name: "Подтвердить точное изменение" }).click();
  await expect(page.getByRole("status").filter({ hasText: "running · 42%" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "completed · 100% · applied" })).toBeVisible();
  await expect(page.getByText("Версия 2 · Google Drive")).toBeVisible();
  await page.getByRole("button", { name: "Явно откатить" }).click();
  await expect(page.getByRole("status").filter({ hasText: "completed · 100% · rolled_back" })).toBeVisible();
  await expect(page.getByText("Версия 3 · Google Drive")).toBeVisible();
  const writes = mock.requests.filter((row) => row.method === "POST" && row.path.includes("storage-mutations"));
  expect(writes).toHaveLength(2);
  expect(JSON.parse(writes[0].body!)).toEqual({ proposal_id: 81, action_id: 82, record_version: 1 });
  expect(JSON.parse(writes[1].body!)).toEqual({ proposal_id: 81, action_id: 82, record_version: 2 });
});

test("late preview from the old project is discarded", async ({ page, mock }) => {
  mock.reply("GET", "/organizer/proposals?project_id=2", { body: { proposals: [proposal] } });
  mock.reply("GET", "/projects/1/storage-mutations/81/actions/82/prepare", { status: 409, body: { detail: "scope changed" } });
  const held = mock.hold((url: URL) => url.pathname.endsWith("/storage-mutations/81/actions/82/prepare"));
  await start(page);
  await page.getByRole("button", { name: "Предложения", exact: true }).click();
  await held.request;
  await page.getByRole("combobox").first().selectOption("1");
  await release(page, held, preview());
  await settled(page);
  await expect(page.getByText("old.pdf → standard.pdf")).toHaveCount(0);
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
});

test("viewer-like denial never retries or claims success", async ({ page, mock }) => {
  mock.reply("GET", "/projects/2/storage-mutations/81/actions/82/prepare", { body: preview() });
  mock.reply("POST", "/projects/2/storage-mutations/confirm", { status: 403, body: { detail: "Insufficient project access" } });
  await openProposal(page, mock);
  await page.getByRole("button", { name: "Подтвердить точное изменение" }).click();
  await expect(page.getByRole("alert")).toContainText("не поставлено в очередь");
  expect(mock.count("/storage-mutations/confirm")).toBe(1);
  await expect(page.getByText(/completed · 100%/)).toHaveCount(0);
});

test("UNKNOWN is visible to the operator and never retried automatically", async ({ page, mock }) => {
  mock.reply("GET", "/projects/2/storage-mutations/81/actions/82/prepare", { body: preview() });
  mock.reply("POST", "/projects/2/storage-mutations/confirm", { body: {
    job_id: 73, project_id: 2, status: "queued", already_queued: false, record_version: 1,
  } });
  mock.reply("GET", "/projects/2/storage-mutations/jobs/73", { body: {
    job_id: 73, project_id: 2, status: "completed", progress: 100, outcome: "unknown", record_version: 2,
  } });
  await openProposal(page, mock);
  await page.getByRole("button", { name: "Подтвердить точное изменение" }).click();
  await expect(page.getByRole("status").filter({ hasText: "completed · 100% · unknown" })).toBeVisible();
  expect(mock.count("/storage-mutations/confirm")).toBe(1);
  await page.waitForTimeout(900);
  expect(mock.count("/storage-mutations/confirm")).toBe(1);
});
