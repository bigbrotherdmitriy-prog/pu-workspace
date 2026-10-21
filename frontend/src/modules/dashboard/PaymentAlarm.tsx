import { useEffect, useState } from "react";
import { api } from "../../api/client";
import "./payment-alarm.css";

type Item = {
  kind: "pay_invoice" | "pay_plan" | "sign_act" | "receive";
  title: string;
  counterparty: string | null;
  contract_id: number | null;
  contract_number?: string | null;
  amount: string;
  due_date: string | null;
  days_left: number | null;
  level: "overdue" | "critical" | "soon" | "near" | "ok" | "none";
};

type Unmarked = { count: number; amount: string; payments: number; receipts: number };

type Response = {
  items: Item[];
  counts: Record<string, number>;
  totals: { to_pay: string; to_receive: string };
  unmarked?: Unmarked;
};

type Side = "receive" | "pay";

type LevelKey = "overdue" | "critical" | "soon";

const LEVEL_LABELS: Record<LevelKey, string> = {
  overdue: "Просрочено",
  critical: "В ближайшие 2 дня",
  soon: "В течение 7 дней",
};

function money(value: number | string): string {
  const num = typeof value === "number" ? value : Number(value);
  const safe = Number.isFinite(num) ? num : 0;
  return `${safe.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

function inSide(item: Item, side: Side): boolean {
  if (side === "receive") return item.kind === "receive";
  return item.kind === "pay_invoice" || item.kind === "pay_plan";
}

function levelOf(item: Item): LevelKey | null {
  if (item.level === "overdue") return "overdue";
  if (item.level === "critical") return "critical";
  if (item.level === "soon") return "soon";
  return null;
}

function itemsSide(items: Item[], side: Side) {
  return items.filter((item) => inSide(item, side));
}

function sumLevel(items: Item[], level: LevelKey): { count: number; amount: number } {
  let amount = 0;
  let count = 0;
  for (const item of items) {
    if (item.level !== level) continue;
    const value = Number(item.amount);
    if (Number.isFinite(value)) amount += value;
    count += 1;
  }
  return { count, amount };
}

function unmarkedCountFor(side: Side, unmarked: Unmarked | undefined): number {
  if (!unmarked) return 0;
  return side === "pay" ? unmarked.payments : unmarked.receipts;
}

function urgencyLabel(item: Item): string {
  if (item.days_left === null) return "без срока";
  if (item.days_left < 0) return `просрочено на ${Math.abs(item.days_left)} дн.`;
  if (item.days_left === 0) return "сегодня";
  return `через ${item.days_left} дн.`;
}

function statusOf(sideItems: Item[]): "overdue" | "critical" | "ok" {
  if (sideItems.some((item) => levelOf(item) === "overdue")) return "overdue";
  if (sideItems.some((item) => levelOf(item) === "critical")) return "critical";
  return "ok";
}

export function PaymentAlarm({ projectId, onOpenFinance }: { projectId: number; onOpenFinance?: () => void }) {
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api(`/projects/${projectId}/money-calendar`)
      .then((res) => {
        if (!alive) return;
        const payload = res as Response | null;
        if (payload && Array.isArray(payload.items)) {
          setData({ ...payload, counts: payload.counts ?? {} });
        } else {
          setData(null);
        }
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  if (error) {
    return (
      <section className="payment-alarm" aria-label="Сигнализация оплат">
        <div className="payment-alarm__error" role="alert">{error}</div>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="payment-alarm" aria-label="Сигнализация оплат">
        <div className="payment-alarm__loading">Загрузка…</div>
      </section>
    );
  }

  const receiveItems = itemsSide(data.items, "receive");
  const payItems = itemsSide(data.items, "pay");

  return (
    <section className="payment-alarm" aria-label="Сигнализация оплат">
      <div className="payment-alarm__grid">
        <AlarmCard
          side="receive"
          title="Приходы"
          items={receiveItems}
          unmarked={data.unmarked}
          onOpenFinance={onOpenFinance}
        />
        <AlarmCard
          side="pay"
          title="Расходы"
          items={payItems}
          unmarked={data.unmarked}
          onOpenFinance={onOpenFinance}
        />
      </div>
    </section>
  );
}

function AlarmCard({ side, title, items, unmarked, onOpenFinance }: {
  side: Side;
  title: string;
  items: Item[];
  unmarked: Unmarked | undefined;
  onOpenFinance?: () => void;
}) {
  const status = statusOf(items);
  const unmarkedCount = unmarkedCountFor(side, unmarked);
  const unmarkedAmountRaw = unmarked?.amount;
  const unmarkedAmount = typeof unmarkedAmountRaw === "string" ? unmarkedAmountRaw : "";
  const headingSuffix = status === "overdue" ? " — есть просрочка" : "";

  const urgent = [...items]
    .filter((item) => levelOf(item) !== null)
    .sort((a, b) => {
      const aDays = a.days_left ?? Number.POSITIVE_INFINITY;
      const bDays = b.days_left ?? Number.POSITIVE_INFINITY;
      return aDays - bDays;
    })
    .slice(0, 3);

  const statusLabel = status === "ok" ? "всё в порядке" : "";

  return (
    <div className={`payment-alarm__card payment-alarm__card--${status}`}>
      <div className="payment-alarm__head">
        <span className={`payment-alarm__dot payment-alarm__dot--${status}`} aria-hidden="true" />
        <h3 className="payment-alarm__title">{title}{headingSuffix}</h3>
      </div>
      {statusLabel && <div className="payment-alarm__status-ok">{statusLabel}</div>}

      <ul className="payment-alarm__levels">
        {(["overdue", "critical", "soon"] as LevelKey[]).map((level) => {
          const { count, amount } = sumLevel(items, level);
          const shownCount = level === "overdue" ? count + unmarkedCount : count;
          const barrier = level;
          return (
            <li key={level} className={`payment-alarm__level payment-alarm__level--${barrier}`}>
              <span className={`payment-alarm__level-marker payment-alarm__level-marker--${barrier}`} aria-hidden="true" />
              <span className="payment-alarm__level-label">{LEVEL_LABELS[level]}</span>
              <span className="payment-alarm__level-count">{shownCount}</span>
              <span className="payment-alarm__level-amount">{money(amount)}</span>
            </li>
          );
        })}
      </ul>

      {unmarked && unmarkedCount > 0 && (
        <div className="payment-alarm__unmarked">
          ещё без отметки: {unmarkedCount} строк{unmarkedAmount ? ` · ${money(unmarkedAmount)}` : ""}
        </div>
      )}

      {urgent.length > 0 && (
        <ul className="payment-alarm__urgent">
          {urgent.map((item, idx) => (
            <li key={`${item.kind}-${idx}-${item.title}`} className="payment-alarm__urgent-row">
              <div className="payment-alarm__urgent-main">
                <span className="payment-alarm__urgent-title">{item.title}</span>
                {item.counterparty && (
                  <span className="payment-alarm__urgent-counterparty">{item.counterparty}</span>
                )}
              </div>
              <span className="payment-alarm__urgent-amount">{money(item.amount)}</span>
              <span className={`payment-alarm__urgent-when payment-alarm__urgent-when--${levelOf(item) ?? "ok"}`}>
                {urgencyLabel(item)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {onOpenFinance && (
        <button type="button" className="payment-alarm__link" onClick={onOpenFinance}>
          Открыть ДДС
        </button>
      )}
    </div>
  );
}
