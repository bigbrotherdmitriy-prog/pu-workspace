import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { formatMoney } from "../../utils/numberFormat";
import "./invoice-quick-confirm.css";

type Proposal = {
  id: number;
  counterparty: string;
  amount: string;
  currency: string;
  payment_purpose: string | null;
  status: string;
  fallback_reason?: string | null;
  planned_date: string | null;
};

type Suggestion = {
  plan_entry_id: number;
  title: string;
  remaining: string;
  reasons: string[];
};

type SuggestionsResponse = {
  suggestions: Suggestion[];
  recommended: number | null;
  additional_recommended: boolean;
  ai_reason?: string;
};

export function InvoiceQuickConfirm({
  projectId,
  proposalId,
  onClose,
  onConfirmed,
}: {
  projectId: number;
  proposalId: number;
  onClose: () => void;
  onConfirmed?: () => void;
}) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [choice, setChoice] = useState<string>("additional");
  const [submitting, setSubmitting] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api<Proposal>(`/execution/invoice-extraction-proposals/${proposalId}`),
      api<SuggestionsResponse>(`/projects/${projectId}/invoice-extraction-proposals/${proposalId}/plan-line-suggestions`),
    ])
      .then(([p, s]) => {
        if (!alive) return;
        setProposal(p);
        setSuggestions(s);
        if (s.recommended != null) setChoice(String(s.recommended));
        else if (s.additional_recommended) setChoice("additional");
        else if (s.suggestions.length > 0) setChoice(String(s.suggestions[0].plan_entry_id));
        else setChoice("additional");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [projectId, proposalId]);

  const selectedSuggestion = suggestions?.suggestions.find((s) => String(s.plan_entry_id) === choice);
  const hint =
    choice === "additional"
      ? suggestions?.ai_reason ?? ""
      : selectedSuggestion && selectedSuggestion.reasons.length > 0
        ? selectedSuggestion.reasons[selectedSuggestion.reasons.length - 1]
        : "";

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const body = choice === "additional" ? { additional: true } : { plan_entry_id: Number(choice) };
      await api(`/execution/invoice-extraction-proposals/${proposalId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setConfirmed(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function handleCloseConfirmed() {
    onConfirmed?.();
    onClose();
  }

  return (
    <div className="iqc-overlay" role="dialog" aria-label="Подтверждение счёта">
      <div className="iqc-card">
        <h2 className="iqc-title">Счёт из почты</h2>
        {loading ? (
          <p>Загружаем счёт…</p>
        ) : proposal && proposal.fallback_reason === "own_outgoing_invoice" && proposal.status === "proposed" ? (
          <>
            <p>Это ваш исходящий счёт: поставщик — ваша компания. В расходы он не записывается.</p>
            <button className="secondary" onClick={onClose}>Закрыть</button>
          </>
        ) : proposal && proposal.status !== "proposed" ? (
          <>
            <p>Этот счёт уже обработан</p>
            <button className="secondary" onClick={onClose}>
              Закрыть
            </button>
          </>
        ) : confirmed ? (
          <>
            <p>Счёт подтверждён</p>
            <button className="secondary" onClick={handleCloseConfirmed}>
              Закрыть
            </button>
          </>
        ) : proposal ? (
          <>
            <div className="iqc-counterparty">{proposal.counterparty}</div>
            <div className="iqc-amount">{formatMoney(proposal.amount)}</div>
            {proposal.payment_purpose ? <div className="iqc-purpose">{proposal.payment_purpose}</div> : null}

            {suggestions ? (
              <div className="iqc-plan-block">
                <label className="iqc-plan-label">Строка плана</label>
                <select
                  aria-label="Строка плана для счёта"
                  value={choice}
                  onChange={(e) => setChoice(e.target.value)}
                >
                  {suggestions.suggestions.map((s) => (
                    <option key={s.plan_entry_id} value={String(s.plan_entry_id)}>
                      {`${s.title} — остаток ${formatMoney(s.remaining)}`}
                    </option>
                  ))}
                  <option value="additional">Дополнительные затраты (вне плана)</option>
                </select>
                {hint ? <div className="iqc-hint">{hint}</div> : null}
                {(() => {
                  const chosen = suggestions.suggestions.find((s) => String(s.plan_entry_id) === choice);
                  const over = chosen ? Number(proposal.amount || 0) - Number(chosen.remaining || 0) : 0;
                  return chosen && over > 0
                    ? <div className="iqc-hint iqc-warning">Счёт больше остатка статьи на {formatMoney(over)} — статья уйдёт в перерасход.</div>
                    : null;
                })()}
              </div>
            ) : null}

            {error ? <p role="alert">{error}</p> : null}

            <button className="iqc-confirm" onClick={handleConfirm} disabled={submitting}>
              {submitting ? "Подтверждаем…" : "Подтвердить"}
            </button>
            <button className="secondary" onClick={onClose}>
              Позже
            </button>
          </>
        ) : error ? (
          <>
            <p role="alert">{error}</p>
            <button className="secondary" onClick={onClose}>
              Позже
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
