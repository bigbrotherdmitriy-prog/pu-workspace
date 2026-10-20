import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";

type PlanVersion = {
  id: number;
  version_number: number;
  label: string;
  effective_from: string | null;
  created_at: string | null;
  totals: { inflow: string; outflow: string };
};

type CompareArticle = {
  title: string;
  direction: string;
  base_total: string;
  target_total: string;
  difference: string;
  months: { month: string; base: string; target: string; difference: string }[];
};

type CompareResult = {
  target_label: string;
  articles: CompareArticle[];
  totals: { inflow: { base: string; target: string; difference: string }; outflow: { base: string; target: string; difference: string } };
  added: string[];
  removed: string[];
};

const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });

function formatDate(value: string | null) {
  if (!value) return "—";
  return dateFormat.format(new Date(`${value.slice(0, 10)}T00:00:00Z`));
}

function differenceClass(difference: string, direction: string) {
  const value = Number(difference);
  if (value === 0) return "";
  const positive = direction === "inflow" ? value > 0 : value < 0;
  return positive ? "positive" : "negative";
}

export function DdsPlanVersions({ projectId, contractId, currency }: { projectId: number; contractId: number | null; currency: string }) {
  const [versions, setVersions] = useState<PlanVersion[]>([]);
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [comparing, setComparing] = useState<{ version: PlanVersion; result: CompareResult } | null>(null);
  const [compareLoading, setCompareLoading] = useState<number | null>(null);

  const load = async () => {
    setError("");
    try {
      const suffix = contractId ? `?contract_id=${contractId}` : "";
      const data = await api<PlanVersion[]>(`/projects/${projectId}/dds-plan-versions${suffix}`);
      setVersions(Array.isArray(data) ? data : []);
    } catch (caught) {
      setError((caught as Error).message);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, contractId]);

  const save = async () => {
    const trimmed = label.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await api(`/projects/${projectId}/dds-plan-versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contract_id: contractId, label: trimmed }),
      });
      setLabel("");
      setSaved(true);
      await load();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const compare = async (version: PlanVersion) => {
    setError("");
    setCompareLoading(version.id);
    try {
      const result = await api<CompareResult>(`/projects/${projectId}/dds-plan-versions/compare?base=${version.id}&target=current`);
      setComparing({ version, result });
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setCompareLoading(null);
    }
  };

  return <section className="dds-plan-versions">
    <div className="dds-filters">
      <label>
        <input
          aria-label="Название версии"
          placeholder="Например: после переноса оплат"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
      </label>
      <button type="button" disabled={!label.trim() || saving} onClick={() => void save()}>Сохранить версию</button>
      {saved && <span>Версия сохранена</span>}
    </div>
    {error && <p role="alert">{error}</p>}
    {versions.length === 0 ? <p className="dds-empty">Версий пока нет. Первая версия «Исходный план» сохранится автоматически при сохранении новой версии.</p> : <div className="dds-table-wrap"><table className="dds-table">
      <thead><tr><th>№</th><th>Название</th><th>Дата</th><th>Приход, {currency}</th><th>Расход, {currency}</th><th></th></tr></thead>
      <tbody>{versions.map((version) => <tr key={version.id}>
        <td>{version.version_number}</td>
        <td>{version.label}</td>
        <td>{formatDate(version.created_at)}</td>
        <td className="money positive">{formatMoney(version.totals?.inflow)}</td>
        <td className="money">{formatMoney(version.totals?.outflow)}</td>
        <td><button className="secondary" type="button" aria-label={`Сравнить версию ${version.label} с текущим планом`} disabled={compareLoading === version.id} onClick={() => void compare(version)}>Сравнить с текущим</button></td>
      </tr>)}</tbody>
    </table></div>}

    {comparing && (() => {
      const { version, result } = comparing;
      const changed = result.articles
        .filter((article) => Number(article.difference) !== 0)
        .sort((a, b) => Math.abs(Number(b.difference)) - Math.abs(Number(a.difference)));
      return <section className="dds-plan-compare">
        <h3>Сравнение: «{version.label}» → текущий план</h3>
        {changed.length === 0 ? <p className="dds-empty">Изменений нет</p> : <div className="dds-table-wrap"><table className="dds-table">
          <thead><tr><th>Статья</th><th>Было</th><th>Стало</th><th>Разница</th></tr></thead>
          <tbody>{changed.map((article) => <tr key={`${article.direction}-${article.title}`}>
            <td>{article.title}</td>
            <td className="money">{formatMoney(article.base_total)}</td>
            <td className="money">{formatMoney(article.target_total)}</td>
            <td className={`money ${differenceClass(article.difference, article.direction)}`}>{formatMoney(article.difference)}</td>
          </tr>)}</tbody>
          <tfoot>
            <tr>
              <td>Итого приход</td>
              <td className="money">{formatMoney(result.totals.inflow.base)}</td>
              <td className="money">{formatMoney(result.totals.inflow.target)}</td>
              <td className={`money ${differenceClass(result.totals.inflow.difference, "inflow")}`}>{formatMoney(result.totals.inflow.difference)}</td>
            </tr>
            <tr>
              <td>Итого расход</td>
              <td className="money">{formatMoney(result.totals.outflow.base)}</td>
              <td className="money">{formatMoney(result.totals.outflow.target)}</td>
              <td className={`money ${differenceClass(result.totals.outflow.difference, "outflow")}`}>{formatMoney(result.totals.outflow.difference)}</td>
            </tr>
          </tfoot>
        </table></div>}
        {result.added.length > 0 && <p>Новые статьи: {result.added.join(", ")}</p>}
        {result.removed.length > 0 && <p>Удалённые статьи: {result.removed.join(", ")}</p>}
        <button className="secondary" type="button" onClick={() => setComparing(null)}>Закрыть сравнение</button>
      </section>;
    })()}
  </section>;
}
