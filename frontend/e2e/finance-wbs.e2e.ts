import { expect, release, settled, start, test } from "./storage-fixtures";
import { financeOverview, installFinanceScenario } from "./finance-wbs-fixtures";

const writes = new Set(["POST", "PUT", "PATCH", "DELETE"]);

test("finance browser: confirmed contract exposes WBS, budget, DDS and advisory forecast without payment effects", async ({ page, mock }) => {
  installFinanceScenario(mock, financeOverview());

  await start(page);
  await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await page.getByRole("combobox").filter({ has: page.locator("option", { hasText: "SYN-31" }) }).selectOption("31");

  await expect(page.getByRole("heading", { name: "Договор → ГПР → бюджет → ДДС → акты" })).toBeVisible();
  for (const step of ["Договор", "ГПР", "Бюджет", "ДДС / счёт"]) {
    await expect(page.getByRole("button", { name: new RegExp(`${step}.*готово`) })).toBeVisible();
  }

  const forecast = page.getByRole("region", { name: "План → обязательства → факт → прогноз" });
  await expect(forecast).toBeVisible();
  await expect(forecast.getByText("Корпус")).toBeVisible();
  await expect(forecast.getByText("Сводный узел")).toBeVisible();
  await expect(forecast.getByText("Фундамент")).toBeVisible();
  await expect(forecast.getByText("Каркас")).toBeVisible();
  await expect(forecast.getByText("Первая дата: 2026-10-04")).toBeVisible();
  await expect(forecast.getByRole("region", { name: "Требуемые решения" }).getByText("OWNER")).toBeVisible();
  await expect(forecast.getByRole("region", { name: "Требуемые решения" }).getByText("LEGAL")).toBeVisible();
  await expect(forecast.getByText(/Автоплатежи, проводки и автоматическая конвертация отключены/)).toBeVisible();
  expect(mock.requests.filter((request) => writes.has(request.method))).toEqual([]);
});

test("finance browser: a late response from another project is hidden fail closed", async ({ page, mock }) => {
  const stale = financeOverview();
  installFinanceScenario(mock, stale);
  const current = financeOverview(1);
  current.summary.cash_gap_date = "2026-12-01";
  mock.reply("GET", "/execution/overview?project_id=1", { body: current });
  mock.reply("GET", "/execution/document-candidates?project_id=1", { body: { candidates: [] } });
  mock.reply("GET", "/execution/forecast/1", { status: 503, body: { detail: "Synthetic forecast disabled" } });
  const pending = mock.hold(url => url.pathname === "/execution/overview" && url.searchParams.get("project_id") === "2");
  await start(page);
  await pending.request;
  await page.getByRole("combobox").first().selectOption("1");
  await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await expect(page.getByText("Первая дата: 2026-12-01")).toBeVisible();
  await release(page, pending, stale);
  await settled(page);
  await expect(page.getByText("Первая дата: 2026-10-04")).toHaveCount(0);
  await expect(page.getByText("Первая дата: 2026-12-01")).toBeVisible();
  expect(mock.requests.filter((request) => writes.has(request.method))).toEqual([]);
});

test("finance browser: a budget line attached to WBS summary is rejected without mutation", async ({ page, mock }) => {
  const overview = financeOverview();
  overview.budget[0].schedule_item_id = 100;
  installFinanceScenario(mock, overview);
  await start(page);
  await page.getByRole("button", { name: "Исполнение и финансы", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Финансовый прогноз скрыт" })).toBeVisible();
  await expect(page.getByText("Сводный узел")).toHaveCount(0);
  expect(mock.requests.filter((request) => writes.has(request.method))).toEqual([]);
});
