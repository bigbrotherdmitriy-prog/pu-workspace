import { useState } from "react";
import { createPortal } from "react-dom";
import { Link2, X } from "lucide-react";
import type { MailContract, MailMessage, MailProject } from "./types";
import type { mailClientApi } from "./mailClientApi";

type Props = {
  message: MailMessage;
  projects: MailProject[];
  contracts: MailContract[];
  client: Pick<typeof mailClientApi, "confirmContext">;
  onDone: () => Promise<void> | void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
};

/**
 * Phone version of the desktop project/contract selectors: «Изменить связь»
 * opens a bottom sheet with the same two choices and saves through the same
 * confirm-context endpoint (with the message's context version, so a stale
 * screen is rejected by the backend instead of silently overwriting). After a
 * save the caller re-fetches the thread, so the link line never shows old data.
 */
export function MobileContextEditor({ message, projects, contracts, client, onDone, onNotice, onError }: Props) {
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState(message.project_id);
  const [contractId, setContractId] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const options = contracts.filter((item) => item.project_id === projectId);
  const titleId = `mail-link-title-${message.id}`;

  function start() {
    setProjectId(message.project_id);
    setContractId(message.contract_id || 0);
    setError("");
    setOpen(true);
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await client.confirmContext(message.id, projectId, contractId || null, message.context_version);
      onNotice("Проект и договор письма подтверждены");
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

  return <div className="mail-mobile-context">
    <button type="button" className="secondary mail-mobile-context-button" onClick={start}>
      <Link2 /> {message.context_confirmed ? "Изменить связь" : "Подтвердить связь"}
    </button>
    {open && createPortal(<div className="mail-mobile-auto-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget && !busy) setOpen(false); }}>
      <section className="mail-mobile-auto-sheet mail-mobile-context-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="mail-mobile-auto-head">
          <h2 id={titleId}>Связь письма</h2>
          <button type="button" aria-label="Закрыть" disabled={busy} onClick={() => setOpen(false)}><X /></button>
        </div>
        <p>Выберите проект и договор, к которым относится письмо.</p>
        <label className="mail-mobile-context-field">Проект
          <select value={projectId} disabled={busy} onChange={(event) => { setProjectId(Number(event.target.value)); setContractId(0); }}>
            {projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label className="mail-mobile-context-field">Договор
          <select value={contractId} disabled={busy} onChange={(event) => setContractId(Number(event.target.value))}>
            <option value={0}>Без договора</option>
            {options.map((item) => <option value={item.id} key={item.id}>{item.number} — {item.title}</option>)}
          </select>
        </label>
        {error && <p className="mail-mobile-auto-error" role="alert">{error}</p>}
        <div className="mail-mobile-auto-actions">
          <button type="button" className="secondary" disabled={busy} onClick={() => setOpen(false)}>Отмена</button>
          <button type="button" disabled={busy || !projectId} onClick={() => void save()}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        </div>
      </section>
    </div>, document.body)}
  </div>;
}
