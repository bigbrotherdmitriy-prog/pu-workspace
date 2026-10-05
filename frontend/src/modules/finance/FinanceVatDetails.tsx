type Snapshot = { mode: string; rate: string | null };
type Row = {
  id: number; status: string; vat_snapshot?: Snapshot | null;
  vat_snapshot_stale?: boolean; vat_refresh_state_hash?: string | null;
  vat_contract_record_version?: number | null; vat_proposed_snapshot?: Snapshot | null;
};

function label(snapshot?: Snapshot | null): string {
  if (!snapshot) return "происхождение не указано";
  if (snapshot.mode === "none") return "без НДС";
  if (snapshot.mode === "unspecified") return "не указан";
  return `${snapshot.rate ?? "?"} % (включён в сумму)`;
}

export function FinanceVatDetails({ row, kind, onRefresh }: {
  row: Row; kind: "budget" | "cash-flow" | "acts";
  onRefresh?: (kind: string, id: number) => void;
}) {
  const canRefresh = onRefresh && row.status === "proposed" && row.vat_refresh_state_hash
    && row.vat_contract_record_version && (!row.vat_snapshot || row.vat_snapshot_stale);
  return <div className="finance-vat-details">
    <small>НДС: {label(row.vat_snapshot)}</small>
    {canRefresh && <>
      <small>{row.vat_snapshot_stale ? "Условия договора изменились. " : "Историческое происхождение не определено. "}
        {row.vat_proposed_snapshot ? `Предлагается: ${label(row.vat_proposed_snapshot)}.` : "Принять условия текущей версии договора."}
        {" "}Сумма, даты и статус сохраняются.</small>
      <button type="button" className="secondary" onClick={() => onRefresh?.(kind, row.id)}>Обновить условия НДС</button>
    </>}
  </div>;
}
