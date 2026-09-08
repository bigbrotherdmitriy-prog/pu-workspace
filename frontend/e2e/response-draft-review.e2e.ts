import { expect, start, test, type StorageApi } from "./storage-fixtures";

const draft = { id: 9, subject: "Synthetic reply", body: "Synthetic reply body", recipient_to: "recipient@example.test",
  status: "draft", confidence: 1, review_token: "a".repeat(64) };
function seed(mock: StorageApi) {
  mock.currentUser = { id: 900, name: "Synthetic Manager", is_admin: false };
  mock.membersByProject.set(2, [{ membership_id: 1, user_id: 900, name: "Synthetic Manager", role: "manager" }]);
  mock.inboxByProject.set(2, [{ id: 101, project_id: 2, source_type: "email", source_name: "Synthetic draft review",
    source_sender: "sender@example.test", content: "Synthetic source", summary: "Synthetic summary", attachments: [],
    context_confidence: 1, context_evidence: "synthetic", context_confirmed: true, status: "new", created_at: "2026-09-08T08:00:00Z",
    tasks: [], drafts: [draft], risks: [], completion_suggestions: [], evidence_refs: [] }]);
  mock.reply("GET", "/response-drafts?project_id=2", { body: { drafts: [draft] } });
}
async function open(page: Parameters<typeof start>[0]) {
  await start(page);
  await page.getByRole("button", { name: "Письма", exact: true }).first().click();
  await page.locator("article.inbox-card").filter({ hasText: "Synthetic draft review" }).getByRole("button", { name: "Открыть", exact: true }).click();
  await page.getByRole("button", { name: "Загрузить черновик для проверки" }).click();
  await expect(page.getByLabel("Тема ответа")).toHaveValue(draft.subject);
}
test("actual App saves then explicitly approves and sends only its reviewed token to the synthetic API", async ({ page, mock }) => {
  seed(mock);
  mock.reply("PATCH", "/response-drafts/9", request => {
    const input = request.postDataJSON();
    if (input.status === "draft") {
      expect(input.expected_review_token).toBe(draft.review_token);
      return { body: { ...draft, body: "Synthetic revised body", review_token: "b".repeat(64) } };
    }
    expect(input).toEqual({ status: "approved", expected_review_token: "b".repeat(64) });
    return { body: { ...draft, body: "Synthetic revised body", status: "approved", review_token: "c".repeat(64) } };
  });
  mock.reply("POST", "/response-drafts/9/send-gmail", { body: { id: 9, status: "queued", job_id: 8, already_sent: false } });
  await open(page);
  await page.getByLabel("Текст ответа").fill("Synthetic revised body");
  await expect(page.getByRole("button", { name: "Подтвердить черновик", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Сохранить правки черновика" }).click();
  await expect(page.getByText(/Правки сохранены\. Проверьте/)).toBeVisible();
  await page.getByRole("button", { name: "Подтвердить черновик", exact: true }).click();
  await expect(page.getByText("Подтверждён — не отправлен", { exact: true })).toBeVisible();
  expect(mock.requests.filter(r => r.method === "POST")).toHaveLength(0);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Отправить через Gmail", exact: true }).click();
  await expect.poll(() => mock.requests.filter(r => r.method === "POST").length).toBe(1);
  const send = mock.requests.find(r => r.method === "POST");
  expect(send?.path).toBe("/response-drafts/9/send-gmail");
  expect(JSON.parse(send?.body || "{}")).toEqual({ expected_review_token: "c".repeat(64) });
  await expect(page.getByText("Ответ поставлен в очередь. Отправка ещё не подтверждена.", { exact: true })).toBeVisible();
  await expect(page.getByText("Ответ отправлен через Gmail и записан в аудит", { exact: true })).toHaveCount(0);
});
test("actual App preserves a conflicted local draft and never retries its write", async ({ page, mock }) => {
  seed(mock);
  mock.reply("PATCH", "/response-drafts/9", { status: 409, body: { detail: "private synthetic raw detail" } });
  await open(page);
  await page.getByLabel("Текст ответа").fill("Retained synthetic edits");
  await page.getByRole("button", { name: "Сохранить правки черновика" }).click();
  await expect(page.getByText(/Версия изменилась или результат неизвестен/)).toBeVisible();
  await expect(page.getByLabel("Текст ответа")).toHaveValue("Retained synthetic edits");
  await expect(page.getByText("private synthetic raw detail", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Сохранить правки черновика" })).toBeDisabled();
  expect(mock.requests.filter(r => r.method === "PATCH")).toHaveLength(1);
  expect(mock.requests.filter(r => r.method === "POST")).toHaveLength(0);
});
