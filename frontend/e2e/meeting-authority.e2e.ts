import { expect, test } from "./storage-fixtures";

const completedMeeting = (canManage = true) => ({
  id: 71,
  record_version: 2,
  project_id: 2,
  title: "Совещание по акту",
  scheduled_at: null,
  duration_minutes: null,
  participants: null,
  participant_user_ids: [],
  participant_contact_ids: [],
  participant_refs: [],
  agenda: null,
  minutes: "Подготовить акт до 25.09.2026",
  status: "completed",
  has_conflicts: false,
  conflict_count: 0,
  conflicts: [],
  can_edit: canManage,
  can_manage: canManage,
});

test("binds exact local-upload evidence then confirms one proposal separately", async ({ page, mock }) => {
  mock.meetings = [completedMeeting()];
  await page.goto("/new/");
  await page.getByRole("button", { name: "Совещания" }).click();

  await expect(page.getByLabel("Источник протокола Совещание по акту")).toHaveValue(
    mock.meetingSource.materialization_id,
  );
  await page.getByRole("button", { name: "Привязать точную версию и подготовить предложения" }).click();

  await expect(page.getByText("Точная версия источника подтверждена")).toBeVisible();
  await expect(page.getByLabel("Безопасные действия: Совещание по акту").locator("blockquote")).toContainText(
    "Подготовить акт до 25.09.2026",
  );
  await expect(page.getByText(/Действия ещё не применены/)).toBeVisible();
  expect(mock.count("/source-binding")).toBe(1);
  expect(mock.count("/meeting-proposals/501/confirm")).toBe(0);
  const bindRequest = mock.requests.find((request) => request.path.includes("/source-binding"));
  expect(JSON.parse(bindRequest?.body || "{}")).toMatchObject({
    expected_record_version: 2,
    source_id: mock.meetingSource.source_id,
    source_version_id: mock.meetingSource.source_version_id,
    evidence_id: mock.meetingSource.evidence_id,
    materialization_id: mock.meetingSource.materialization_id,
  });

  await page.getByRole("button", { name: "Подтвердить только это действие" }).click();
  await expect(page.getByText("Применено: task #901")).toBeVisible();
  expect(mock.count("/meeting-proposals/501/confirm")).toBe(1);
});

test("shows stale source conflict truthfully and never implies an applied action", async ({ page, mock }) => {
  mock.meetings = [completedMeeting()];
  mock.meetingBindingReply = {
    status: 409,
    body: { detail: { code: "record_version_conflict" } },
  };
  await page.goto("/new/");
  await page.getByRole("button", { name: "Совещания" }).click();
  await page.getByRole("button", { name: "Привязать точную версию и подготовить предложения" }).click();
  await expect(page.getByRole("alert")).toContainText("Протокол изменился");
  await expect(page.getByText(/Действия ещё не применены/)).not.toBeVisible();
  expect(mock.count("/meeting-proposals/501/confirm")).toBe(0);
});

test("viewer sees evidence but no binding or confirmation controls", async ({ page, mock }) => {
  mock.meetings = [completedMeeting(false)];
  mock.meetingProposals.set(71, [{
    id: 501,
    record_version: 1,
    proposal_type: "task",
    payload: { title: "Подготовить акт", excerpt: "Подготовить акт до 25.09.2026" },
    status: "proposed",
  }]);
  await page.goto("/new/");
  await page.getByRole("button", { name: "Совещания" }).click();
  await expect(page.getByLabel("Безопасные действия: Совещание по акту").locator("blockquote")).toContainText(
    "Подготовить акт до 25.09.2026",
  );
  await expect(page.getByText("Ожидает подтверждения manager.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Подтвердить только это действие" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Привязать точную версию и подготовить предложения" })).toHaveCount(0);
});
