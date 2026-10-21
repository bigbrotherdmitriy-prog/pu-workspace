import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./contract-stages.css";

type StageAct = { id: number; number: string; status: string; act_date: string | null };
type StageItem = {
  id: number;
  object_name: string | null;
  stage_number: string | number;
  title: string;
  planned_start: string | null;
  planned_finish: string | null;
  amount: string;
  coefficient: string;
  vat: string;
  act: StageAct | null;
  planned_receipt_date: string | null;
  actual_receipt_date: string | null;
  overdue: boolean;
};
type ObjectTotals = { price: string; acted: string; received: string; remaining: string };
type StageRegisterResponse = {
  items: StageItem[];
  by_object: Record<string, ObjectTotals>;
  totals: Record<string, unknown>;
  next_act_number: string;
};
type ExtractResponse = { warnings?: string[] } & Partial<StageRegisterResponse>;

const asNumberString = (value: unknown): string => typeof value === "string" ? value : value == null ? "" : String(value);
const fmtDate = (value: string | null | undefined): string => {
  if (!value) return "—";
  const d = value.slice(0, 10).split("-");
  if (d.length !== 3) return value;
  return `${d[2]}.${d[1]}.${d[0]}`;
};
const fmtPeriod = (start: string | null, finish: string | null): string => {
  if (!start && !finish) return "—";
  if (start && finish) return `с ${fmtDate(start)} по ${fmtDate(finish)}`;
  if (start) return `с ${fmtDate(start)}`;
  return `по ${fmtDate(finish)}`;
};
const actStatusLabel = (status: string): string => {
  switch (status) {
    case "draft": return "черновик";
    case "signed": return "подписан";
    case "paid": return "оплачен";
    default: return status;
  }
};

export function ContractStagesRegister({ projectId, contractId, currency = "RUB" }: { projectId: number; contractId: number; currency?: string }) {
  const [data, setData] = useState<StageRegisterResponse | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signingInput, setSigningInput] = useState<{ actId: number; value: string } | null>(null);
  const [actNumber, setActNumber] = useState("");
  const [actNumberTouched, setActNumberTouched] = useState(false);

  const load = useCallback(async ({ resetActNumber = false }: { resetActNumber?: boolean } = {}) => {
    try {
      const result = await api<StageRegisterResponse>(`/projects/${projectId}/contracts/${contractId}/stage-register`);
      setData(result);
      if (resetActNumber || !actNumberTouched) {
        setActNumber(asNumberString(result?.next_act_number));
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, contractId, actNumberTouched]);

  useEffect(() => { void load(); }, [load]);

  const onExtract = async () => {
    setBusy(true);
    try {
      const result = await api<ExtractResponse>(`/projects/${projectId}/contracts/${contractId}/stages/extract`, { method: "POST" });
      setWarnings(result?.warnings ?? []);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onGenerateAct = async (stageId: number) => {
    setBusy(true);
    try {
      await api(`/projects/${projectId}/contracts/${contractId}/stages/${stageId}/act`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number: actNumber }),
      });
      setActNumberTouched(false);
      await load({ resetActNumber: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onSign = async (actId: number, actDate: string) => {
    setBusy(true);
    try {
      await api(`/projects/${projectId}/acceptance-acts/${actId}/signed`, {
        method: "POST",
        body: JSON.stringify({ act_date: actDate }),
      });
      setSigningInput(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const items = data?.items ?? [];

  if (!data) {
    return error ? <p role="alert">{error}</p> : null;
  }

  if (items.length === 0) {
    return <>
      <p>Этапы договора ещё не извлечены</p>
      <button type="button" disabled={busy} onClick={() => void onExtract()}>Найти этапы в договоре</button>
      {warnings.length > 0 && <ul style={{ color: "#888" }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {error && <p role="alert">{error}</p>}
    </>;
  }

  // Group items by object_name preserving order
  const groups: { objectName: string | null; items: StageItem[] }[] = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.objectName === item.object_name) last.items.push(item);
    else groups.push({ objectName: item.object_name, items: [item] });
  }

  const colCount = 10;

  return <>
    <label>
      Номер следующего акта
      {" "}
      <input
        aria-label="Номер следующего акта"
        value={actNumber}
        onChange={(e) => { setActNumber(e.target.value); setActNumberTouched(true); }}
      />
    </label>
    <div style={{ overflowX: "auto" }}>
      <table className="contract-stages-table">
        <thead>
          <tr>
            <th>Этап</th>
            <th>Работы</th>
            <th>Сроки</th>
            <th>Сумма, {currency}</th>
            <th>Коэф.</th>
            <th>НДС</th>
            <th>Акт</th>
            <th>Приход план</th>
            <th>Приход факт</th>
            <th>Действие</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group, gi) => (
            <StagesGroup
              key={gi}
              group={group}
              colCount={colCount}
              busy={busy}
              signingInput={signingInput}
              setSigningInput={setSigningInput}
              onGenerateAct={onGenerateAct}
              onSign={onSign}
              projectId={projectId}
            />
          ))}
        </tbody>
      </table>
    </div>
    {Object.entries(data.by_object ?? {}).map(([name, totals]) => (
      <p key={name}>{name}: цена {formatMoney(totals.price)}, актировано {formatMoney(totals.acted)}, получено {formatMoney(totals.received)}, осталось {formatMoney(totals.remaining)}</p>
    ))}
    {warnings.length > 0 && <ul style={{ color: "#888" }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
    {error && <p role="alert">{error}</p>}
  </>;
}

function StagesGroup({
  group, colCount, busy, signingInput, setSigningInput, onGenerateAct, onSign, projectId,
}: {
  group: { objectName: string | null; items: StageItem[] };
  colCount: number;
  busy: boolean;
  signingInput: { actId: number; value: string } | null;
  setSigningInput: (v: { actId: number; value: string } | null) => void;
  onGenerateAct: (stageId: number) => Promise<void>;
  onSign: (actId: number, actDate: string) => Promise<void>;
  projectId: number;
}) {
  return <>
    {group.objectName !== null && (
      <tr className="contract-stages-object">
        <th colSpan={colCount} style={{ textAlign: "left" }}>{group.objectName}</th>
      </tr>
    )}
    {group.items.map((item) => (
      <StageRow
        key={item.id}
        item={item}
        busy={busy}
        signingInput={signingInput}
        setSigningInput={setSigningInput}
        onGenerateAct={onGenerateAct}
        onSign={onSign}
        projectId={projectId}
      />
    ))}
  </>;
}

function StageRow({
  item, busy, signingInput, setSigningInput, onGenerateAct, onSign, projectId,
}: {
  item: StageItem;
  busy: boolean;
  signingInput: { actId: number; value: string } | null;
  setSigningInput: (v: { actId: number; value: string } | null) => void;
  onGenerateAct: (stageId: number) => Promise<void>;
  onSign: (actId: number, actDate: string) => Promise<void>;
  projectId: number;
}) {
  const act = item.act;
  const isDraft = act != null && act.status !== "signed" && act.status !== "paid";
  const isSigning = act != null && signingInput?.actId === act.id;

  return (
    <tr className={item.overdue ? "overdue" : undefined}>
      <td>{item.stage_number}-й</td>
      <td title={item.title}><span className="contract-stages-work">{item.title}</span></td>
      <td>{fmtPeriod(item.planned_start, item.planned_finish).replace("с ", "").replace(" по ", " – ")}</td>
      <td>{formatMoney(item.amount)}</td>
      <td>{item.coefficient}</td>
      <td>{formatMoney(Number(item.vat) || 0)}</td>
      <td>{act ? <span className={`contract-stages-act ${act.status}`}>№ {act.number} · {actStatusLabel(act.status)}</span> : "—"}</td>
      <td>
        {fmtDate(item.planned_receipt_date)}
        {item.overdue && <> <span>просрочен</span></>}
      </td>
      <td>{fmtDate(item.actual_receipt_date)}</td>
      <td>
        {!act && (
          <button type="button" disabled={busy} aria-label={`Сформировать акт по этапу ${item.stage_number}`}
            onClick={() => void onGenerateAct(item.id)}>Сформировать акт</button>
        )}
        {act && (
          <>
            <button type="button" onClick={() => window.open(`/projects/${projectId}/acceptance-acts/${act.id}/docx`)}>Скачать Word</button>
            {isDraft && !isSigning && (
              <button type="button" disabled={busy} onClick={() => setSigningInput({ actId: act.id, value: "" })}>Подписан</button>
            )}
            {isDraft && isSigning && (
              <>
                <input type="date" aria-label="Дата подписания акта" value={signingInput?.value ?? ""}
                  onChange={(e) => setSigningInput({ actId: act.id, value: e.target.value })} />
                <button type="button" disabled={busy || !signingInput?.value}
                  onClick={() => void onSign(act.id, signingInput?.value ?? "")}>Сохранить</button>
              </>
            )}
          </>
        )}
      </td>
    </tr>
  );
}
