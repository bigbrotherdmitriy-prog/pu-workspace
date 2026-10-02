import { expect, test, settled } from "./storage-fixtures";
import type { Locator } from "@playwright/test";

async function readableOptions(select: Locator) {
  const styles = await select.locator("option").evaluateAll(options => options.map(option => {
    const style = getComputedStyle(option);
    const channels = (value: string) => (value.match(/[\d.]+/g) || []).map(Number);
    const luminance = (rgb: number[]) => rgb.slice(0, 3).map(value => value / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    const foreground = luminance(channels(style.color));
    const background = channels(style.backgroundColor);
    const backgroundLuminance = luminance(background);
    return {
      text: option.textContent,
      opaque: background.length >= 3 && (background[3] ?? 1) === 1,
      contrast: (Math.max(foreground, backgroundLuminance) + .05)
        / (Math.min(foreground, backgroundLuminance) + .05),
    };
  }));
  expect(styles.length).toBeGreaterThan(0);
  for (const style of styles) {
    expect(style.opaque, `${style.text}: native popup needs its own opaque surface`).toBe(true);
    expect(style.contrast, `${style.text}: option text contrast`).toBeGreaterThanOrEqual(4.5);
  }
}

for (const theme of ["light", "dark"]) {
  test(`native project and finance options remain readable in ${theme}`, async ({ page, mock: _mock }) => {
    await page.addInitScript(theme => localStorage.setItem("pu-display-preferences-v1",
      JSON.stringify({ theme, comfort: false })), theme);
    await page.goto("/new/");
    const project = page.getByRole("combobox", { name: "Текущий проект", exact: true });
    await expect(project.locator("option")).toHaveCount(2);
    await expect(page.locator(".shell")).toHaveClass(/future-light-dashboard/);
    await settled(page);

    // Work Center deliberately has a permanently dark header, even in light mode.
    await readableOptions(project);
    await expect(project).toHaveCSS("color-scheme", "dark");
    await page.locator("aside nav").getByRole("button", { name: "Сегодня", exact: true }).click();
    await settled(page);
    await readableOptions(project);
    await expect(project).toHaveCSS("color-scheme", theme);
    await project.selectOption("1");
    await expect(project).toHaveValue("1");
    await project.selectOption("2");
    await expect(project).toHaveValue("2");

    await page.locator("aside nav").getByRole("button", { name: "ГПР и ДДС", exact: true }).click();
    await page.getByRole("tab", { name: "ДДС", exact: true }).click();
    const filters = page.locator(".dds-filters select");
    expect(await filters.count()).toBeGreaterThan(0);
    for (const filter of await filters.all()) await readableOptions(filter);
    await page.getByRole("button", { name: "Закрыть ГПР и ДДС", exact: true }).click();
    await expect(project).toHaveValue("2");
  });
}
