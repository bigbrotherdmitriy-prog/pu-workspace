import { expect, release, settled, start, test } from "./storage-fixtures";
import { attention, digestPreference, history, installManagement, obligation } from "./management-fixtures";

const management = (page: Parameters<typeof start>[0]) => page.getByRole("region", { name: "Центр управления проектом" });

test("M3 browser: attention filters exact rows and low-confidence obligation fails closed on CAS conflict", async ({ page, mock }) => {
  const low = obligation(17, 2, { review: true, title: "Передать закрывающий акт" });
  installManagement(mock, 2, [
    attention("obligation", 17, { review: true, title: low.title }),
    attention("task", 18, { overdue: true, title: "Подготовить исполнительную схему" }),
    attention("risk", 19, { review: true, title: "Риск задержки поставки" }),
    attention("decision", 20, { title: "Решение по замене материала" }),
  ], [low]);
  mock.reply("GET", "/management/v2/obligations/17/history", history("obligation", 17));
  mock.reply("PATCH", "/management/v2/obligations/17", { status: 409, body: { detail: "version_conflict" } });

  await start(page);
  const center = management(page);
  await expect(center.getByRole("heading", { name: "Требует внимания" })).toBeVisible();
  await center.getByRole("group", { name: "Фильтр внимания" }).getByRole("button", { name: "Обязательства" }).click();
  await expect(center.getByRole("button", { name: /Передать закрывающий акт/ })).toBeVisible();
  await expect(center.getByRole("button", { name: /Подготовить исполнительную схему/ })).toHaveCount(0);
  await center.getByRole("button", { name: /Передать закрывающий акт/ }).click();
  await expect(center.getByRole("alert").filter({ hasText: "Низкая уверенность" })).toBeVisible();
  await expect(center.getByRole("button", { name: "В работу" })).toBeDisabled();
  await center.getByRole("button", { name: "Подтвердить" }).click();
  await expect(center.getByRole("region", { name: low.title }).getByRole("alert").filter({ hasText: "изменена другим пользователем" })).toBeVisible();
  expect(mock.count("/management/v2/obligations/17")).toBe(2); // history + one PATCH; never an automatic retry.
  const patch = mock.requests.find(row => row.method === "PATCH" && row.path === "/management/v2/obligations/17");
  expect(JSON.parse(patch?.body || "{}")).toMatchObject({ expected_version: 3, status: "confirmed" });
});

test("M3 browser: risk and decision remain evidence-backed and perform no provider action", async ({ page, mock }) => {
  installManagement(mock, 2, [
    attention("risk", 19, { review: true, title: "Риск задержки поставки" }),
    attention("decision", 20, { title: "Решение по замене материала" }),
  ]);
  mock.reply("GET", "/management/v2/risks/19/history?project_id=2", history("risk", 19));
  mock.reply("GET", "/management/v2/decisions/20/history?project_id=2", history("decision", 20));
  mock.reply("PATCH", "/management/v2/decisions/20", { body: { id: 20, record_version: 4 } });

  await start(page);
  const center = management(page);
  await center.getByRole("group", { name: "Фильтр внимания" }).getByRole("button", { name: "Риски" }).click();
  await center.getByRole("button", { name: /Риск задержки поставки/ }).click();
  await expect(center.getByRole("button", { name: "Закрыть риск" })).toBeDisabled();
  await expect(center.getByText("Доказательство synthetic-evidence-17")).toBeVisible();
  await center.getByRole("group", { name: "Фильтр внимания" }).getByRole("button", { name: "Решения" }).click();
  await center.getByRole("button", { name: /Решение по замене материала/ }).click();
  await center.getByRole("button", { name: "Зафиксировать исполнение" }).click();
  await expect(center.getByRole("region", { name: "Решение по замене материала" }).getByRole("status").filter({ hasText: "Изменение сохранено" })).toBeVisible();
  expect(mock.requests.some(row => /provider|gmail|telegram|calendar|google\/tasks/i.test(row.path))).toBe(false);
});

test("M3 browser: persisted weekdays, quiet hours and channel are saved before durable digest enqueue", async ({ page, mock }) => {
  installManagement(mock, 2, [], []);
  mock.reply("PUT", "/management/v2/projects/2/digest-preference", request => {
    const body = JSON.parse(request.postData() || "{}");
    return { body: { ...digestPreference(2, 900, 5), timezone: body.timezone, quiet_start: `${body.quiet_start}:00`,
      quiet_end: `${body.quiet_end}:00`, channel: body.channel, cadence: body.cadence } };
  });
  mock.reply("POST", "/management/v2/digests", { body: { job_id: 441, status: "queued", external_actions_created: false } });

  await start(page);
  const center = management(page);
  await center.getByLabel("Тихие часы с").fill("21:30");
  await center.getByRole("textbox", { name: "до", exact: true }).fill("07:15");
  await center.getByLabel("Расписание").selectOption("weekdays");
  await center.getByLabel("Канал").selectOption("in_app");
  await center.getByRole("button", { name: "Сохранить настройки" }).click();
  await expect(center.getByRole("status").filter({ hasText: "Настройки сводки сохранены" })).toBeVisible();
  await center.getByRole("button", { name: "Сформировать сводку сейчас" }).click();
  await expect(center.getByText("Задание сводки № 441")).toBeVisible();
  await expect(center.getByText("Состояние очереди: queued")).toBeVisible();
  expect(JSON.parse(mock.requests.find(row => row.method === "PUT")?.body || "{}")).toMatchObject({
    expected_version: 4, quiet_start: "21:30", quiet_end: "07:15", cadence: "weekdays", channel: "in_app",
  });
  expect(mock.requests.some(row => /provider|gmail|telegram|calendar|google\/tasks/i.test(row.path))).toBe(false);
  await expect(center.getByText(/\d+%/)).toHaveCount(0);
});

test("M3 browser: a late previous-project response cannot replace the selected project", async ({ page, mock }) => {
  installManagement(mock, 2, [attention("risk", 29, { title: "STALE project two risk" })]);
  installManagement(mock, 1, [attention("decision", 31, { title: "CURRENT project one decision" })]);
  const pending = mock.hold(url => url.pathname === "/management/v2/attention" && url.searchParams.get("project_id") === "2");

  await page.goto("/new/");
  await pending.request;
  await page.getByRole("combobox").first().selectOption("1");
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  await expect(management(page).getByText("CURRENT project one decision")).toBeVisible();
  await release(page, pending, { items: [attention("risk", 29, { title: "STALE project two risk" })], total: 1,
    offset: 0, limit: 50, generated_at: "2026-09-05T10:00:00Z", external_actions_created: false });
  await settled(page);
  await expect(management(page).getByText("CURRENT project one decision")).toBeVisible();
  await expect(management(page).getByText("STALE project two risk")).toHaveCount(0);
});

test("M3 browser: a late mutation result cannot leak into another project", async ({ page, mock }) => {
  const old = obligation(17, 2, { title: "Old project obligation" });
  installManagement(mock, 2, [attention("obligation", 17, { title: old.title })], [old]);
  installManagement(mock, 1, [attention("decision", 31, { title: "Current project decision" })]);
  mock.reply("GET", "/management/v2/obligations/17/history", history("obligation", 17));
  const pending = mock.hold(url => url.pathname === "/management/v2/obligations/17");

  await start(page);
  const center = management(page);
  await center.getByRole("button", { name: /Old project obligation/ }).click();
  await center.getByRole("button", { name: "Подтвердить" }).click();
  await pending.request;
  await page.getByRole("combobox").first().selectOption("1");
  await expect(center.getByText("Current project decision")).toBeVisible();
  await release(page, pending, { detail: "version_conflict" }, 409);
  await expect(center.getByText("Current project decision")).toBeVisible();
  await expect(center.getByText(/изменена другим пользователем/)).toHaveCount(0);
});

test("M3 browser: viewer cannot mutate governed records and other product sections keep management intact", async ({ page, mock }) => {
  mock.currentUser = { id: 900, name: "Synthetic Viewer", email: "viewer@example.invalid", is_admin: false };
  mock.membersByProject.set(2, [{ membership_id: 71, user_id: 900, name: "Synthetic Viewer", email: "viewer@example.invalid", role: "viewer" }]);
  const item = obligation(17, 2, { title: "Viewer protected obligation" });
  installManagement(mock, 2, [attention("obligation", 17, { title: item.title })], [item]);
  mock.reply("GET", "/management/v2/obligations/17/history", history("obligation", 17));
  mock.reply("GET", "/execution/forecast/2", { body: {
    forecast_id: "synthetic-forecast-2", project_id: 2, as_of: "2026-09-05T10:00:00Z", publication_state: "draft",
    advisory_only: true, can_trigger_actions: false, requires_human_confirmation: true,
    confidence: { score: 0.5, band: "medium", formula: "synthetic", low_confidence_threshold: 0.4 },
    schedule: { formula: "synthetic", predicted_finish: null, stages: [] },
    budget: { formula: "synthetic", planned_total: "0", forecast_total: "0", variance: "0", lines: [] },
    cash_flow: { formula: "synthetic", opening_balance: "0", closing_balance: "0", minimum_balance: "0", cash_gap_date: null, events: [] },
    risks: [], manual_confirmation: { binding: "synthetic-forecast-2", required_before: [], reason: "synthetic",
      persistence_available: false },
  } });

  await start(page);
  const center = management(page);
  await center.getByRole("button", { name: /Viewer protected obligation/ }).click();
  await expect(center.getByText("Для изменения требуется роль менеджера проекта.")).toBeVisible();
  await expect(center.getByRole("button", { name: "Подтвердить" })).toBeDisabled();
  await expect(center.getByRole("button", { name: "В работу" })).toBeDisabled();
  expect(mock.requests.some(row => row.method === "PATCH" && row.path.includes("/management/"))).toBe(false);

  await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Исполнение и финансы" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Рабочий центр", exact: true }).click();
  await expect(management(page).getByText("Viewer protected obligation")).toBeVisible();
});
