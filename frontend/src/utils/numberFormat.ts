const RU_NUMBER_FORMAT = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  useGrouping: true,
});

export function formatNumber(value?: number | string | null): string {
  const numericValue = typeof value === "string"
    ? Number(value.replace(/\s/g, "").replace(",", "."))
    : Number(value ?? 0);

  return RU_NUMBER_FORMAT.format(Number.isFinite(numericValue) ? numericValue : 0);
}

export function formatMoney(value?: number | string | null): string {
  return `${formatNumber(value)} ₽`;
}
