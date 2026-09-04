import { expect, settled, start, test, type StorageApi } from "./storage-fixtures";

const INERT_HTML = '<img src=x onerror="globalThis.pwned=true"><script>globalThis.pwned=true</script>';

function evidence(id: string, options: { verified?: boolean; confidence?: number; excerpt?: string } = {}) {
  const verified = options.verified ?? true;
  const versionId = `version-${id}`;
  const sourceId = `source-${id}`;
  return {
    schema_version: "evidence-fragment.v54.2",
    state: "readable",
    status: verified ? "verified" : "unverified",
    version_state: "current",
    freshness: "fresh",
    availability: "available",
    valid_until: "2030-09-04T12:00:00Z",
    evidence: { id, revision: 1, source_id: sourceId, source_version_id: versionId },
    source: {
      id: sourceId,
      record_version: 4,
      current_source_version_id: versionId,
      provider: "synthetic-provider",
      account: "synthetic-account",
      namespace: "synthetic-mailbox",
      origin_project: "Новый проект",
    },
    source_version: { id: versionId, revision: 2, source_id: sourceId },
    locator: { kind: "message", message_external_id: `message-${id}`, part: "body", char_range: [4, 30] },
    fragment: { media_type: "text/plain", excerpt: options.excerpt ?? `Exact synthetic excerpt ${id}` },
    extracted_fact: `Exact synthetic fact ${id}`,
    ai_conclusion: "Требуется проверка в интерфейсе.",
    extractor: {
      name: "synthetic-extractor",
      version: "1",
      method: "fixture",
      model_provider: "synthetic",
      model_id: "no-network-model",
      model_version: "1",
      prompt_version: "synthetic-prompt-1",
    },
    confidence: { value: options.confidence ?? 0.91, kind: "model", calibration_ref: null },
    assessment: {
      verification: verified ? "verified" : "unverified",
      reviewer: verified ? "Synthetic Reviewer" : null,
      reviewed_at: verified ? "2026-09-04T09:00:00Z" : null,
      record_version: 3,
    },
  };
}

function inboxMessage(id: number, evidenceIds: string[] = [], attachments: Record<string, unknown>[] = []) {
  return {
    id,
    project_id: 2,
    source_type: "email",
    source_name: `Synthetic mail ${id}`,
    source_sender: "sender@example.invalid",
    content: "Synthetic source body. No provider document was accessed.",
    attachments,
    summary: "Synthetic summary for isolated browser verification.",
    context_confidence: 1,
    context_evidence: "explicit synthetic project binding",
    context_confirmed: true,
    status: "new",
    created_at: "2026-09-04T08:00:00Z",
    tasks: [],
    drafts: [],
    risks: [],
    completion_suggestions: [],
    evidence_refs: evidenceIds.map(evidenceId => ({ id: evidenceId, revision: 1 })),
  };
}

async function openMail(page: Parameters<typeof start>[0], messageName: string) {
  await start(page);
  await page.getByRole("button", { name: "Письма", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Входящие письма" })).toBeVisible();
  await page.locator("article.inbox-card").filter({ hasText: messageName })
    .getByRole("button", { name: "Открыть", exact: true }).click();
}

function setInbox(mock: StorageApi, rows: Record<string, unknown>[]) {
  mock.inboxByProject.set(2, rows);
}

test("v54 readable exact evidence and low-confidence manual review render in the real App", async ({ page, mock }) => {
  setInbox(mock, [inboxMessage(101, ["verified-101", "manual-101"])]);
  mock.evidenceReplies.set("verified-101", { body: evidence("verified-101") });
  mock.evidenceReplies.set("manual-101", { body: evidence("manual-101", { verified: false, confidence: 0.125, excerpt: INERT_HTML }) });

  await openMail(page, "Synthetic mail 101");

  const panel = page.getByRole("region", { name: "Доказательства" });
  await expect(panel.locator(".evidence-fragment-card")).toHaveCount(2);
  await expect(panel.getByText("verified-101/r1", { exact: true })).toBeVisible();
  await expect(panel.getByText("version-verified-101/r2", { exact: true })).toBeVisible();
  await expect(panel.getByText(INERT_HTML, { exact: true })).toBeVisible();
  await expect(panel.getByText(/Оценка извлечения: 12,5\s*%/)).toBeVisible();
  await expect(panel.getByText("Фрагмент доступен только для проверки. Confidence не заменяет решение человека.")).toBeVisible();
  await expect(panel.locator("img, script")).toHaveCount(0);
  expect(await page.evaluate(() => (globalThis as typeof globalThis & { pwned?: boolean }).pwned)).not.toBe(true);
});

test("v54 stale, unavailable and cross-scope decisions uniformly deny content", async ({ page, mock }) => {
  setInbox(mock, [inboxMessage(102, ["stale-102", "unavailable-102", "cross-scope-102"])]);
  const stale = evidence("stale-102", { excerpt: "FORBIDDEN-STALE-CONTENT" });
  stale.freshness = "stale";
  mock.evidenceReplies.set("stale-102", { body: stale });
  mock.evidenceReplies.set("unavailable-102", { body: {
    schema_version: "evidence-fragment.v54.2", state: "unavailable", status: "unavailable", reason_code: "resource_unavailable",
  } });
  mock.evidenceReplies.set("cross-scope-102", { body: {
    schema_version: "evidence-fragment.v54.2", state: "unavailable", status: "unavailable", reason_code: "policy_denied",
  } });

  await openMail(page, "Synthetic mail 102");

  const cards = page.locator(".evidence-fragment-card");
  await expect(cards).toHaveCount(3);
  await expect(cards.getByRole("heading", { name: "Доказательство недоступно" })).toHaveCount(3);
  await expect(cards.locator("blockquote, code")).toHaveCount(0);
  await expect(page.getByText("FORBIDDEN-STALE-CONTENT", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Exact synthetic fact/)).toHaveCount(0);
  await expect(page.getByText("synthetic-account", { exact: true })).toHaveCount(0);
});

test("v54 late evidence cannot flash stale content or return navigation to the old project", async ({ page, mock }) => {
  setInbox(mock, [inboxMessage(103, ["late-103"])]);
  mock.inboxByProject.set(1, []);
  mock.evidenceReplies.set("late-103", { body: evidence("late-103", { excerpt: "OLD-PROJECT-EVIDENCE" }) });
  await openMail(page, "Synthetic mail 103");
  await expect(page.getByText("OLD-PROJECT-EVIDENCE", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Свернуть", exact: true }).click();
  const pending = mock.hold(url => url.pathname.includes("/evidence/late-103/fragment"));
  await page.getByRole("button", { name: "Открыть", exact: true }).click();
  await pending.request;
  await expect(page.getByRole("status", { name: "" }).filter({ hasText: "Проверяем доступ" })).toBeVisible();
  await expect(page.getByText("OLD-PROJECT-EVIDENCE", { exact: true })).toHaveCount(0);

  await page.getByRole("combobox").first().selectOption("1");
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  pending.release({ body: evidence("late-103", { excerpt: "FORBIDDEN-LATE-REPLY" }) });
  await settled(page);
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  await expect(page.getByText(/OLD-PROJECT-EVIDENCE|FORBIDDEN-LATE-REPLY/)).toHaveCount(0);
  await expect(page).toHaveURL(/\/new\/$/);
});

test("mailbox attachment import exposes API progress and the indexed UI state", async ({ page, mock }) => {
  setInbox(mock, [inboxMessage(104, [], [{
    name: "synthetic-attachment.pdf", mime_type: "application/pdf", size: 2048, attachment_id: "attachment-104", imported: false,
  }])]);
  mock.attachmentImportReply = { body: { name: "synthetic-attachment.pdf", already_indexed: false, tasks: 2, risks: 1, drafts: 0 } };
  await openMail(page, "Synthetic mail 104");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Импортировать и проанализировать" }).click();

  await expect(page.getByText("Вложение «synthetic-attachment.pdf» добавлено: задач 2, рисков 1, черновиков 0.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Уже в документах" })).toBeVisible();
  const request = mock.requests.find(row => row.path === "/ai-secretary/inbox/104/attachments/0/import");
  expect(request?.method).toBe("POST");
  expect(request?.body).toBeNull();
});

test("existing local-upload and AI-policy entries use isolated API routes", async ({ page, mock }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await start(page);
  await page.getByRole("button", { name: "Добавить документ" }).click();
  const upload = page.getByRole("dialog", { name: "Загрузка документов с Android" });
  await upload.locator('input[type="file"]').first().setInputFiles("e2e/fixtures/synthetic-note.txt");
  await upload.getByRole("button", { name: "Загрузить и проанализировать (1)" }).click();
  await expect(page.getByText("Обработано: 1. Задач: 1. Рисков: 0. Пропущено: 0.")).toBeVisible();
  const uploadRequest = mock.requests.find(row => row.path === "/local-upload/analyze");
  expect(uploadRequest?.method).toBe("POST");
  expect(JSON.parse(uploadRequest?.body || "{}")).toMatchObject({ project_id: 2, files: [{ path: "synthetic-note.txt", mime_type: "text/plain" }] });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Интеграции", exact: true }).first().click();
  const policyCard = page.locator("article.integration-card").filter({ has: page.getByRole("heading", { name: "Политика AI", exact: true }) });
  await policyCard.getByRole("button", { name: "Политика AI", exact: true }).click();
  await page.getByLabel("Режим обработки").selectOption("redacted");
  await page.getByRole("button", { name: "Сохранить политику" }).click();
  await expect(page.getByText("Политика AI и защиты данных сохранена")).toBeVisible();
  expect(mock.aiPolicies.get(2)).toMatchObject({ project_id: 2, mode: "redacted", dlp_enabled: true, prompt_version: "v1" });
  const policyRequest = mock.requests.find(row => row.path === "/projects/2/ai-policy" && row.method === "PATCH");
  expect(JSON.parse(policyRequest?.body || "{}")).toEqual({ mode: "redacted", dlp_enabled: true });
});
