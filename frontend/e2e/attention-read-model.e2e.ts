import { expect, test } from "./storage-fixtures";

test("shows a project-scoped attention item with origin and opens its section", async ({ page, mock }) => {
  mock.attentionItems = [{
    kind: "risk", entity_id: 81, status: "confirmed", priority: "high",
    project_id: 2, contract_id: null, owner_user_id: 900,
    title: "Риск задержки поставки", effective_date: "2026-09-26",
    explanation: "Открытый риск: критичность high",
    origin: { type: "meeting_source", id: "source-81", source_version_id: "version-81" },
    navigation: { section: "Риски и решения", project_id: 2, entity_type: "risk", entity_id: 81 },
  }];

  await page.goto("/new/");
  await page.getByRole("button", { name: "AI Secretary", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Требует внимания", exact: true })).toBeVisible();
  const card = page.getByRole("article").filter({ hasText: "Риск задержки поставки" });
  await expect(card).toContainText("meeting_source · source-81");
  await card.getByRole("button", { name: /Открыть/ }).click();
  await expect(page.getByText("Риски и решения", { exact: true }).first()).toBeVisible();
});
