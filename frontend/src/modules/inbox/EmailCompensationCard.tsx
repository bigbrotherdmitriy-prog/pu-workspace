import { useState } from "react";
import { AlertTriangle, Reply } from "lucide-react";

export type EmailCompensationOffer = {
  direct_undo_possible: false;
  message: string;
  status: "AVAILABLE" | "PROPOSED" | "UNAVAILABLE";
  can_propose: boolean;
  source_action_id?: string;
  source_revision?: number;
  source_etag?: string;
  approval_mode?: "CONFIRM";
  unavailable_reason?: "source_unavailable" | "source_stale" | "source_ambiguous";
  proposal?: {
    action_id: string;
    revision: number;
    state: "PROPOSED";
    ledger_state: string;
    approval_mode: "CONFIRM";
    draft_id: number;
  };
};

type Props = {
  offer?: EmailCompensationOffer;
  onPropose: (offer: EmailCompensationOffer) => Promise<void>;
};

const unavailableText: Record<string, string> = {
  source_unavailable: "Исходное действие недоступно. Подготовка ответа заблокирована.",
  source_stale: "Исходное действие изменилось или устарело. Обновите данные.",
  source_ambiguous: "Не удалось однозначно определить исходную отправку.",
};

export function EmailCompensationCard({ offer, onPropose }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const isProposed = offer?.status === "PROPOSED" && Boolean(offer.proposal);
  const canPropose = Boolean(
    offer?.status === "AVAILABLE"
      && offer.can_propose
      && offer.source_action_id
      && offer.source_revision
      && offer.source_etag
      && offer.approval_mode === "CONFIRM",
  );

  async function propose() {
    if (!offer || !canPropose || submitting) return;
    setSubmitting(true);
    try {
      await onPropose(offer);
    } finally {
      setSubmitting(false);
    }
  }

  return <section className="email-compensation" aria-label="Корректирующий ответ">
    <div className="email-compensation__warning">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>Отменить отправку нельзя</strong>
        <p>Исходное письмо останется отправленным. Исправление оформляется новым ответом.</p>
      </div>
    </div>
    {isProposed ? (
      <p className="email-compensation__status" role="status">
        Корректирующий ответ подготовлен как черновик. Перед отправкой потребуется отдельное подтверждение.
      </p>
    ) : (
      <>
        <button type="button" disabled={!canPropose || submitting} onClick={propose}>
          <Reply aria-hidden="true" />
          {submitting ? "Подготавливаем…" : "Подготовить корректирующий ответ"}
        </button>
        {!canPropose && <p className="email-compensation__status" role="status">
          {unavailableText[offer?.unavailable_reason || "source_unavailable"]}
        </p>}
      </>
    )}
  </section>;
}
