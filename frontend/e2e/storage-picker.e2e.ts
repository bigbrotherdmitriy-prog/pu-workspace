import { test, expect, start, open, picker, paths, discovery, folder, binding, release, settled, specialPath, type Provider } from "./storage-fixtures";

test("01 new project stays selected beside Persistent Project", async ({ page, mock }) => {
  await start(page); await open(page);
  await expect(picker(page).getByRole("button", { name: "Выбрать текущую папку" })).toBeEnabled();
  await expect(page.getByRole("combobox").first()).toHaveValue("2");
  expect(mock.requests.filter(row => row.path.includes("/source-folders/")).every(row => row.path.startsWith("/projects/2/"))).toBe(true);
});

for (const provider of ["google_drive", "yandex_disk"] as Provider[]) {
  test(`02-04 ${provider}: selected provider, nested folders, encoded confirmation, parent navigation`, async ({ page, mock }, info) => {
    mock.provider = provider;
    await start(page);
    await page.getByRole("button", { name: "Интеграции", exact: true }).click();
    await page.locator("article.integration-card").filter({ has: page.getByRole("heading", { name: provider === "google_drive" ? "Google Drive" : "Яндекс Диск", exact: true }) })
      .getByRole("button", { name: "Выбрать папку", exact: true }).click();
    await expect(picker(page).getByRole("button", { name: "Заказчик", exact: true })).toBeVisible();
    const first = new URL(mock.requests.find(row => row.path.includes("/discover"))!.path, "http://localhost");
    expect(first.searchParams.get("provider")).toBe(provider);
    expect(first.searchParams.has("folder_id")).toBe(false);
    for (const name of ["Проект #1", "Этап ? 50%"] ) {
      await picker(page).locator("article").filter({ has: page.locator("strong", { hasText: name }) }).getByRole("button", { name: "Открыть", exact: true }).click();
      await expect(picker(page).locator(".source-breadcrumbs").getByRole("button", { name, exact: true })).toBeDisabled();
    }
    await picker(page).getByRole("button", { name: "Выбрать текущую папку" }).click();
    await expect(picker(page).getByRole("status").filter({ hasText: "Выбрана:" })).toContainText("задание №42");
    const confirmation = mock.requests.find(row => row.path.includes("/snapshot-queue"))!;
    const encoded = encodeURIComponent(paths[provider][3]);
    expect(confirmation.method).toBe("POST");
    expect(confirmation.path).toContain(`/projects/2/source-folders/${encoded}/snapshot-queue?`);
    const url = new URL(confirmation.path, "http://localhost");
    expect(url.searchParams.get("connection_id")).toBe(binding(provider).connection_id);
    expect(url.searchParams.get("provider")).toBe(provider);
    expect(url.hash).toBe("");
    if (provider === "yandex_disk") expect(decodeURIComponent(encoded)).toBe(specialPath);
    expect(mock.requests.some(row => row.method === "PUT" || row.path.includes("/yandex/root"))).toBe(false);
    await expect(page.getByRole("combobox").first()).toHaveValue("2");
    await info.attach(`${provider}-synthetic-selection`, { body: await page.screenshot(), contentType: "image/png" });
    await picker(page).getByRole("button", { name: /^Заказчик/ }).click();
    await expect(picker(page).locator("article").getByText("Проект #1", { exact: true })).toBeVisible();
  });
}

test("05 reverse discovery order keeps newest folder", async ({ page, mock }) => {
  await start(page); await open(page);
  await expect(picker(page).getByRole("button", { name: "Выбрать текущую папку" })).toBeEnabled();
  const first = mock.hold(url => url.searchParams.get("folder_id") === paths.google_drive[2]);
  const second = mock.hold(url => url.searchParams.get("folder_id") === "root");
  await picker(page).getByRole("button", { name: "Открыть", exact: true }).click();
  await first.request;
  await picker(page).getByRole("button", { name: /^Мой диск/ }).click();
  await second.request;
  await release(page, second, discovery("google_drive", "root"));
  await expect(picker(page).locator(".source-breadcrumbs button")).toHaveCount(1);
  await release(page, first, discovery("google_drive", paths.google_drive[2]));
  await expect(picker(page).locator(".source-breadcrumbs button")).toHaveCount(1);
  await expect(picker(page).locator("article").getByText("Заказчик", { exact: true })).toBeVisible();
});

test("06 project changes before discovery reply", async ({ page, mock }) => {
  const pending = mock.hold(url => url.pathname.endsWith("/discover"));
  await start(page); await open(page); await pending.request;
  await page.getByRole("combobox").first().selectOption("1");
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  await release(page, pending, discovery("google_drive"));
  await expect(picker(page)).toHaveCount(0);
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  expect(mock.count("/snapshot-queue")).toBe(0);
});

test("07/09 late confirmation caches only its project and cannot load it again", async ({ page, mock }) => {
  const pending = mock.hold(url => url.pathname.endsWith("/snapshot-queue"));
  await start(page); await open(page);
  await picker(page).getByRole("button", { name: "Выбрать текущую папку" }).click();
  const request = await pending.request;
  expect(request.method()).toBe("POST");
  expect(request.headers()["x-csrf-token"]).toBe("synthetic-csrf-only");
  await page.getByRole("combobox").first().selectOption("1");
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  await settled(page);
  const count = mock.requests.length;
  await release(page, pending, mock.confirm(paths.google_drive[1]));
  await expect(picker(page)).toHaveCount(0);
  await expect(page.getByRole("combobox").first()).toHaveValue("1");
  expect(mock.requests.slice(count).some(row => row.path === "/projects/" || row.path.startsWith("/projects/2/"))).toBe(false);
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("pu_storage_selection_v1:2")!).job_id)).toBe(42);
  expect(await page.evaluate(() => sessionStorage.getItem("pu_storage_selection_v1:1"))).toBeNull();
});

test("08 close/reopen invalidates the previous discovery", async ({ page, mock }) => {
  const pending = mock.hold(url => url.pathname.endsWith("/discover"));
  await start(page); await open(page); await pending.request;
  await picker(page).getByRole("button", { name: "Закрыть", exact: true }).click();
  mock.roots.set(2, paths.google_drive[3]);
  await open(page);
  await expect(picker(page).getByRole("button", { name: "Этап ? 50%", exact: true })).toBeDisabled();
  await release(page, pending, discovery("google_drive", "root"));
  await expect(picker(page).getByRole("button", { name: "Этап ? 50%", exact: true })).toBeDisabled();
  expect(mock.count("/discover")).toBe(2);
});

test("10 real reload restores project, saved folder, snapshot and job", async ({ page, mock }) => {
  mock.provider = "yandex_disk"; mock.roots.set(2, specialPath);
  await start(page); await open(page);
  await picker(page).getByRole("button", { name: "Выбрать текущую папку" }).click();
  await expect(picker(page).getByRole("status").filter({ hasText: "Выбрана:" })).toContainText("задание №42");
  mock.snapshots[0].status = "ready"; mock.snapshots[0].analysis_status = "failed";
  mock.snapshots[0].analysis_error = "Синтетическая безопасная ошибка";
  await page.reload();
  await expect(page.getByRole("combobox").first()).toHaveValue("2");
  await open(page);
  await expect(picker(page).getByRole("status").filter({ hasText: "Выбрана:" })).toContainText("статус снимка: ready");
  await expect(picker(page).getByRole("status").filter({ hasText: "Выбрана:" })).toContainText("анализ: failed");
  await expect(picker(page).getByRole("status").filter({ hasText: "Выбрана:" })).toContainText("задание №42");
  await expect(picker(page).getByRole("button", { name: "Этап ? 50%", exact: true })).toBeDisabled();
  expect(mock.count("/snapshot-queue")).toBe(1);
});

for (const endpoint of ["discovery", "confirmation"] as const) {
  test(`11 ${endpoint} 409 requires explicit reopen, no retry loop`, async ({ page, mock }) => {
    const conflict = { status: 409, body: { detail: "Selected storage connection changed" } };
    if (endpoint === "discovery") mock.discoveryReply = () => conflict;
    else mock.confirmReply = conflict;
    await start(page); await page.clock.install(); await open(page);
    if (endpoint === "confirmation") await picker(page).getByRole("button", { name: "Выбрать текущую папку" }).click();
    await expect(picker(page).getByRole("alert")).toContainText("Переоткройте выбор папки");
    await expect(picker(page).getByRole("button", { name: "Переоткрыть выбор" })).toBeVisible();
    await expect(picker(page).getByRole("button", { name: "Выбрать текущую папку" })).toHaveCount(0);
    await page.clock.runFor(16_000);
    expect(mock.count("/discover")).toBe(1);
    expect(mock.count("/snapshot-queue")).toBe(endpoint === "confirmation" ? 1 : 0);
    mock.discoveryReply = undefined; mock.confirmReply = undefined;
    await picker(page).getByRole("button", { name: "Переоткрыть выбор" }).click();
    await expect(picker(page).getByRole("button", { name: "Выбрать текущую папку" })).toBeEnabled();
  });
}

test("11 wrong provider cannot silently open another account", async ({ page, mock }) => {
  mock.provider = "yandex_disk";
  await start(page);
  await page.getByRole("button", { name: "Интеграции", exact: true }).click();
  await page.locator("article.integration-card").filter({ has: page.getByRole("heading", { name: "Google Drive", exact: true }) })
    .getByRole("button", { name: "Выбрать папку", exact: true }).click();
  await expect(picker(page).getByRole("alert")).toContainText("сначала выберите его подключение");
  expect(mock.requests.some(row => row.method !== "GET")).toBe(false);
});

for (const action of ["standardize", "analyze"] as const) {
  test(`12 ${action} already_queued is not completed`, async ({ page, mock }) => {
    const result = discovery("google_drive");
    result.folders[0] = { ...result.folders[0], registered: true, is_primary: true, snapshot_id: 31, snapshot_status: "ready",
      analysis_result: action === "analyze" ? { mode: "safe_copy" } : null };
    mock.discoveryReply = () => ({ body: result });
    await start(page); await open(page);
    await picker(page).getByRole("button", { name: action === "analyze" ? "Подготовить стандарт рабочей папки" : "Создать копию и стандартизировать", exact: true }).click();
    await expect(page.getByText(action === "analyze" ? /Готовится таблица «Было → Станет»/ : /задание уже существует \(retrying\)/)).toBeVisible();
    await expect(page.getByText(/обработка завершена/)).toHaveCount(0);
    expect(mock.count(`/${action}`)).toBe(1);
  });
}

test("13 processing shows measured counts or unknown, never invented percentages", async ({ page, mock }, info) => {
  const result = discovery("google_drive");
  result.folders = [
    { ...folder("google_drive", paths.google_drive[2], "Без измерений"), registered: true, snapshot_id: 31, snapshot_status: "building" },
    { ...folder("google_drive", paths.google_drive[3], "С измерениями"), registered: true, snapshot_id: 32, snapshot_status: "ready",
      analysis_status: "analyzing", analysis_result: { organizer_session_id: 42 }, item_count: 20 },
  ];
  mock.discoveryReply = () => ({ body: result });
  mock.queue = { summary: { active: 1, failed: 0, dead_letter: 0 }, snapshots: [], sessions: [
    { id: 42, status: "running", progress: 37, source_item_count: 20, copy_item_count: 20, processed_item_count: 7, queue_position: null, retry_count: 0 },
  ] };
  await start(page); await open(page);
  await expect(picker(page).getByLabel("Обработка: процент не предоставлен сервером")).toContainText("Количество обработанных объектов пока неизвестно");
  await expect(picker(page).getByLabel("Прогресс анализа 37%")).toContainText("7 обработано · 13 осталось");
  await expect(picker(page).getByLabel("Прогресс анализа 37%")).toContainText("running");
  await expect(picker(page).getByText(/^(5|10)%$/)).toHaveCount(0);
  await info.attach("synthetic-real-progress", { body: await page.screenshot(), contentType: "image/png" });
});

test("explicit missing project never falls back after reload", async ({ page, mock }) => {
  mock.projectRows = mock.projectRows.slice(0, 1);
  await page.goto("/new/");
  await expect(page.getByText(/Проект №2 отсутствует в ответе сервера/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("combobox").first()).toHaveValue("2");
  await expect(page.getByText(/Проект №2 отсутствует в ответе сервера/)).toBeVisible();
  expect(mock.requests.some(row => row.path.startsWith("/projects/1/"))).toBe(false);
});
