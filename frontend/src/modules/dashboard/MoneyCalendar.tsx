import { useEffect, useState } from "react";
import { api } from "../../api/client";
import "./money-calendar.css";

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

type Response = {
  items: Item[];
  counts: Record<string, number>;
  totals: { to_pay: string; to_receive: string };
  unmarked?: { count: number; amount: string; payments: number; receipts: number };
};

function formatMoney(value: string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return num.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const kindLabels: Record<Item["kind"], string> = {
  pay_invoice: "Оплатить счёт",
  pay_plan: "Оплата по плану",
  sign_act: "Подписать акт",
  receive: "Ждём приход",
};

function badgeText(item: Item): string {
  if (item.days_left === null) return "без срока";
  if (item.days_left < 0) return `⚠ просрочено на ${Math.abs(item.days_left)} дн.`;
  if (item.days_left === 0) return "сегодня";
  return `через ${item.days_left} дн.`;
}

const actionLabels: Record<Item["kind"], string> = {
  pay_invoice: "Отметить оплату",
  pay_plan: "Отметить оплату",
  sign_act: "К актам",
  receive: "Отметить приход",
};

export type MoneyCalendarItem = Item;

export function MoneyCalendar({ projectId, onOpenFinance, onOpenItem }: { projectId: number; onOpenFinance?: () => void; onOpenItem?: (item: Item) => void }) {
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api(`/projects/${projectId}/money-calendar`)
      .then((res) => { if (alive) setData(res && Array.isArray((res as Response).items) ? { ...(res as Response), counts: (res as Response).counts ?? {} } : null); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [projectId]);

  if (error) {
    return <div className="money-calendar" role="alert">{error}</div>;
  }
  if (!data) {
    return <div className="money-calendar">Загрузка…</div>;
  }

  const list = showAll ? data.items : data.items.slice(0, 12);

  return (
    <section className="money-calendar" aria-label="Ближайшие сроки по деньгам">
      <div className="money-calendar-header">
        <h3>Ближайшие сроки по деньгам</h3>
        {onOpenFinance && (
          <button type="button" className="money-calendar-link" onClick={onOpenFinance}>Финансы</button>
        )}
      </div>
      <div className="money-calendar-chips">
        <span className="money-calendar-chip level-overdue">Просрочено {data.counts.overdue ?? 0}</span>
        <span className="money-calendar-chip level-critical">≤ 2 дней {data.counts.critical ?? 0}</span>
        <span className="money-calendar-chip level-soon">≤ 7 дней {(data.counts.soon ?? 0) + (data.counts.critical ?? 0)}</span>
      </div>
      {data.unmarked && data.unmarked.count > 0 && (
        <button type="button" className="money-calendar-unmarked" onClick={onOpenFinance}>
          ⚠ Без отметки об оплате: {data.unmarked.payments} плановых платежей и {data.unmarked.receipts} приходов старше двух недель
          на {formatMoney(data.unmarked.amount)} ₽ — отметьте, что уже оплачено и получено
        </button>
      )}
      {data.items.length === 0 ? (
        <div className="money-calendar-empty">В ближайшие 45 дней сроков нет</div>
      ) : (
        <ul className="money-calendar-list">
          {list.map((item, idx) => {
            const label = kindLabels[item.kind];
            const badge = badgeText(item);
            return (
              <li
                key={`${item.kind}-${idx}-${item.title}`}
                className={`money-calendar-row level-${item.level}`}
                aria-label={`${label}: ${item.title}, ${badge}`}
              >
                <span className={`money-calendar-bar level-${item.level}`} aria-hidden="true" />
                <div className="money-calendar-main">
                  <span className={`money-calendar-badge level-${item.level}`}>{badge}</span>
                  <span className="money-calendar-kind">{label}</span>
                  <span className="money-calendar-title">{item.title}</span>
                  {item.counterparty && <span className="money-calendar-counterparty">{item.counterparty}</span>}
                  {item.contract_number && <span className="money-calendar-contract">№ {item.contract_number}</span>}
                </div>
                <div className="money-calendar-amount">{formatMoney(item.amount)} ₽</div>
                {onOpenItem && <button type="button" className="money-calendar-action" onClick={() => onOpenItem(item)}>{actionLabels[item.kind]}</button>}
              </li>
            );
          })}
        </ul>
      )}
      {!showAll && data.items.length > 12 && (
        <button type="button" className="money-calendar-more" onClick={() => setShowAll(true)}>Показать все</button>
      )}
    </section>
  );
}
