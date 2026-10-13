import { useState } from "react";
import { createPortal } from "react-dom";
import { ShieldCheck, X } from "lucide-react";
import type { MailContract, MailMessage } from "./types";
import type { mailClientApi } from "./mailClientApi";

type Props = {
  message: MailMessage;
  contracts: MailContract[];
  client: Pick<typeof mailClientApi, "confirmContext" | "confirmContextForAuto">;
  onDone: () => Promise<void> | void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
};

/**
 * Phone shortcut for "pick a contract → Изменить связь → Подтвердить контекст
 * для AUTO": one button opens the contract choice and then calls the same two
 * endpoints in order. The second call carries the context version returned by
 * the first, so every backend check (role, contract ownership, version) still
 * applies exactly as on desktop.
 */
export function MobileAutoConfirm({ message, contracts, client, onDone, onNotice, onError }: Props) {
  const [open, setOpen] = useState(false);
  const [contractId, setContractId] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const options = contracts.filter((item) => item.project_id === message.project_id);

  function start() {
    setContractId(message.contract_id && options.some((item) => item.id === message.contract_id) ? message.contract_id : 0);
    setError("");
    setOpen(true);
  }

  async function confirm() {
    if (!contractId || busy) return;
    setBusy(true);
    setError("");
    try {
      const linked = await client.confirmContext(message.id, message.project_id, contractId, message.context_version);
      if (!linked.context_version || linked.contract_id !== contractId) {
        throw new Error("Связь письма не подтвердилась, AUTO не включён. Обновите письмо и повторите.");
      }
      await client.confirmContextForAuto(message.id, linked.project_id, contractId, linked.context_version);
      onNotice("Договор и контекст письма подтверждены для AUTO");
      setOpen(false);
      await onDone();
    } catch (reason) {
      const text = (reason as Error).message;
      setError(text);
      onError(text);
    } finally {
      setBusy(false);
    }
  }

  return <div className="mail-mobile-auto">
    <button type="button" className="mail-mobile-auto-button" disabled={message.ordinary_read_only} onClick={start}>
      <ShieldCheck /> Подтвердить для AUTO
    </button>
    {open && createPortal(<div className="mail-mobile-auto-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget && !busy) setOpen(false); }}>
      <section className="mail-mobile-auto-sheet" role="dialog" aria-modal="true" aria-labelledby={`mail-auto-title-${message.id}`}>
        <div className="mail-mobile-auto-head">
          <h2 id={`mail-auto-title-${message.id}`}>Договор письма для AUTO</h2>
          <button type="button" aria-label="Закрыть" disabled={busy} onClick={() => setOpen(false)}><X /></button>
        </div>
        <p>Выберите договор. Система подтвердит связь письма с ним и затем контекст для AUTO.</p>
        {options.length ? <fieldset disabled={busy}>
          <legend>Договор</legend>
          {options.map((item) => <label key={item.id} className={contractId === item.id ? "selected" : ""}>
            <input type="radio" name={`mail-auto-contract-${message.id}`} value={item.id}
              checked={contractId === item.id} onChange={() => setContractId(item.id)} />
            <span><b>{item.number}</b>{item.title}</span>
          </label>)}
        </fieldset> : <p className="mail-mobile-auto-empty">В проекте письма нет договоров. Без договора AUTO не включается.</p>}
        {error && <p className="mail-mobile-auto-error" role="alert">{error}</p>}
        <div className="mail-mobile-auto-actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => setOpen(false)}>Отмена</button>
          <button type="button" disabled={!contractId || busy} onClick={() => void confirm()}>{busy ? "Подтверждаю…" : "Подтвердить"}</button>
        </div>
      </section>
    </div>, document.body)}
  </div>;
}
