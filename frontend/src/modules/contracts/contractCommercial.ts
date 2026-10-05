export type VatMode = "unspecified" | "none" | "rate";
export type VatSnapshot = {
  schema_version: number; mode: VatMode; rate: string | null;
  source_contract_id: number; source_contract_record_version: number;
};

export function contractFieldErrors(details: unknown): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!Array.isArray(details)) return errors;
  for (const item of details) {
    if (item && typeof item === "object" && Array.isArray(item.loc) && typeof item.msg === "string") {
      const field = item.loc[item.loc.length - 1];
      if (typeof field === "string") errors[field] = item.msg;
    }
  }
  return errors;
}

export function contractFieldErrorMessage(errors: Record<string, string>): string {
  const labels: Record<string, string> = { amount: "Сумма", advance_amount: "Аванс", retention_percent: "Удержание",
    signed_at: "Дата подписания", warranty_until: "Гарантия до", performed_from: "Начало исполнения", performed_to: "Окончание исполнения",
    vat_mode: "НДС", vat_rate: "Ставка НДС", number: "Номер", title: "Название", counterparty: "Контрагент", status: "Статус" };
  return Object.entries(errors).map(([field, message]) => `${labels[field] || field}: ${message}`).join(". ");
}

export function vatLabel(mode: VatMode = "unspecified", rate?: string | null): string {
  if (mode === "none") return "Без НДС";
  if (mode === "rate" && rate != null) return `НДС ${rate.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")}%`;
  return "НДС не указан";
}

export function contractMoney(amount: number | string | null | undefined, currency = "RUB"): string {
  if (amount == null) return "Не указана";
  const text = typeof amount === "number" ? amount.toFixed(2) : amount;
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return "Не указана";
  const whole = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  return `${match[1]}${whole},${(match[3] || "").padEnd(2, "0")} ${currency === "RUB" ? "₽" : currency}`;
}

export function inheritedVatLabel(snapshot?: VatSnapshot | null): string {
  return snapshot ? `${vatLabel(snapshot.mode, snapshot.rate)} · условия договора №${snapshot.source_contract_id}, v${snapshot.source_contract_record_version}`
    : "НДС: происхождение условий не зафиксировано";
}
