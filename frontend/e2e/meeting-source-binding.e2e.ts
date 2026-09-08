import { expect, start, test } from "./storage-fixtures";

const source = "10000000-0000-4000-8000-000000000001", version = "10000000-0000-4000-8000-000000000002";
const bindingId = "10000000-0000-4000-8000-000000000003";
const pin = { ref: { namespace: "pu", type: "evidence", tenant_id: { kind: "int", value: "1" },
  id: { kind: "uuid", value: "10000000-0000-4000-8000-000000000004" } }, version_kind: "revision", value: 1 };
const binding = { meeting_id: 5, meeting_record_version: 3, binding_id: bindingId, source_id: source,
  source_version_id: version, origin_status: "bound", confirmation_available: true, external_actions_created: false };
const unbound = { meeting_id: 5, meeting_record_version: 2, origin_status: "invalid_source",
  origin_reason: "meeting_source_binding_required", confirmation_available: false, external_actions_created: false, proposals: [] };

test("M305: human selects exact source, proposes and confirms internal Task in the actual App", async ({ page, mock }) => {
  mock.membersByProject.set(2, [{ membership_id: 1, user_id: 900, name: "Synthetic Operator", role: "owner" }]);
  mock.reply("GET", "/management/meetings?project_id=2", { body: { meetings: [{ id: 5, project_id: 2,
    record_version: 2, title: "Synthetic meeting", minutes: "Synthetic protocol", status: "completed" }] } });
  mock.reply("GET", "/management/v2/meetings/5/eligible-sources?project_id=2", { body: {
    meeting_id: 5, meeting_record_version: 2, sources: [{ source_id: source, source_version_id: version,
      evidence_pins: [pin] }], external_actions_created: false } });
  mock.reply("POST", "/management/v2/meetings/5/source-binding", { body: binding });
  mock.reply("GET", "/management/v2/meetings/5/proposals?project_id=2", { body: unbound });
  const proposal = { ...binding, kind: "task", entity_type: "obligation", entity_id: 9, record_version: 1,
    status: "needs_confirmation", review_state: "needs_review", task_id: null };
  mock.reply("POST", "/management/v2/meetings/5/proposals", { body: { ...binding, proposals: [proposal] } });
  mock.reply("POST", "/management/v2/proposals/obligation/9/confirm", { body: { external_actions_created: false,
    proposal: { ...proposal, record_version: 3, status: "confirmed", review_state: "verified", task_id: 12 } } });
  await start(page);
  await page.getByRole("button", { name: "Совещания", exact: true }).click();
  await page.getByRole("button", { name: "Выбрать источник протокола", exact: true }).click();
  await page.getByLabel("Подтверждённый источник").selectOption(`${source}:${version}`);
  expect(mock.requests.filter(r => r.method === "POST")).toHaveLength(0);
  await page.getByRole("button", { name: "Привязать выбранный источник" }).click();
  await page.getByLabel("Название действия").fill("Synthetic internal action");
  await page.getByLabel("Ответственный", { exact: true }).selectOption("900");
  await page.getByLabel("Доказательство 1", { exact: true }).check();
  await page.getByRole("button", { name: "Подготовить предложение" }).click();
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await expect(page.getByText(/Предложение подтверждено\. Внешние письма/)).toBeVisible();
  const writes = mock.requests.filter(r => r.method === "POST");
  expect(writes).toHaveLength(3);
  expect(JSON.parse(writes[0].body || "{}")).toMatchObject({ project_id: 2, expected_version: 2,
    source_id: source, source_version_id: version });
  expect(JSON.parse(writes[1].body || "{}")).toMatchObject({ meeting_source_binding_id: bindingId });
  expect(JSON.parse(writes[2].body || "{}")).toEqual({ project_id: 2, expected_version: 1, create_internal_task: true });
});

test("M305: stale meeting source reply cannot enable binding or confirmation", async ({ page, mock }) => {
  mock.reply("GET", "/management/v2/meetings/5/proposals?project_id=2", { body: unbound });
  mock.reply("GET", "/management/meetings?project_id=2", { body: { meetings: [{ id: 5, project_id: 2,
    record_version: 2, title: "Synthetic meeting", minutes: "Synthetic protocol", status: "completed" }] } });
  mock.reply("GET", "/management/v2/meetings/5/eligible-sources?project_id=2", { body: {
    meeting_id: 5, meeting_record_version: 3, sources: [], external_actions_created: false } });
  await start(page);
  await page.getByRole("button", { name: "Совещания", exact: true }).click();
  await page.getByRole("button", { name: "Выбрать источник протокола" }).click();
  await expect(page.getByText(/Источник или действие недоступны/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Привязать выбранный источник" })).toHaveCount(0);
  expect(mock.requests.filter(r => r.method === "POST")).toHaveLength(0);
});
