import { useMemo, useState } from "react";
import { Users } from "lucide-react";
import { api } from "../../api/client";

export type ProjectContact = {
  id: number; project_id: number; contract_id?: number; name: string; company?: string;
  email: string; active: boolean; confirmed: boolean; source: string; company_activity?: string;
  record_version: number; resolution_state: "proposed" | "conflict" | "confirmed" | "corrected" | "rejected";
  resolution_reason_code: string; phone?: string;
};
export type ContactContract = { id: number; number: string; title: string };
export type ContactDraft = {
  id: number; subject: string; body: string; status: string; source_file_name: string;
  source_excerpt: string; confidence: number; reviewer_name: string; recipient_to?: string;
};

type Props = {
  projectId: number;
  contacts: ProjectContact[];
  contracts: ContactContract[];
  drafts: ContactDraft[];
  reload: () => Promise<void>;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onUpdateDraft: (draft: ContactDraft, status: string) => void;
  onSendDraft: (draft: ContactDraft) => void;
};

export function ContactsModule({ projectId, contacts, contracts, drafts, reload, onNotice, onError, onUpdateDraft, onSendDraft }: Props) {
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [contractId, setContractId] = useState(0);
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => Object.entries(contacts.reduce<Record<string, ProjectContact[]>>((result, contact) => {
    const key = contact.company?.trim() || "Компания не указана";
    (result[key] ||= []).push(contact);
    return result;
  }, {})), [contacts]);
  const clientDrafts = drafts.filter((draft) => draft.recipient_to);

  async function createContact() {
    if (!name.trim() || !email.trim() || busy) return;
    setBusy(true);
    try {
      await api("/project-contacts", { method: "POST", body: JSON.stringify({
        project_id: projectId, contract_id: contractId || null, name: name.trim(),
        company: company.trim() || null, email: email.trim(),
      }) });
      setName(""); setCompany(""); setEmail(""); setContractId(0);
      onNotice("Контакт клиента сохранён и закреплён за текущим проектом");
      await reload();
    } catch (reason) { onError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmContact(contact: ProjectContact) {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/project-contacts/${contact.id}/resolve`, { method: "POST", body: JSON.stringify({
        decision_key: `contact:${contact.id}:${contact.record_version}:${crypto.randomUUID()}`,
        expected_record_version: contact.record_version,
        decision: "confirm",
        reason_code: "reviewed_by_operator",
      }) });
      onNotice(`Контакт ${contact.email} подтверждён. Следующие письма будут направляться в этот проект.`);
      await reload();
    } catch (reason) { onError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function prepareEmail(contact: ProjectContact) {
    const subject = window.prompt(`Тема письма для ${contact.name}`)?.trim();
    if (!subject) return;
    const body = window.prompt("Текст письма. После создания потребуется отдельное подтверждение перед отправкой.")?.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      await api(`/project-contacts/${contact.id}/draft`, { method: "POST", body: JSON.stringify({ subject, body }) });
      onNotice(`Черновик для ${contact.email} создан. Проверьте его ниже и подтвердите перед отправкой.`);
      await reload();
    } catch (reason) { onError((reason as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="company-directory">
    <section className="card company-create">
      <div><span className="eyebrow">КАРТОЧКА КЛИЕНТА</span><h2>Добавить контакт и закрепить за проектом</h2><p>Входящие от подтверждённого email будут автоматически направляться в текущий проект и выбранный договор.</p></div>
      <div className="company-create-form">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Имя контактного лица" />
        <input value={company} onChange={(event) => setCompany(event.target.value)} placeholder="Компания" />
        <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="client@company.ru" />
        <select value={contractId} onChange={(event) => setContractId(Number(event.target.value))}><option value={0}>Без договора</option>{contracts.map((contract) => <option value={contract.id} key={contract.id}>{contract.number} — {contract.title}</option>)}</select>
        <button disabled={busy || !name.trim() || !email.trim()} onClick={createContact}>{busy ? "Сохраняю…" : "Сохранить контакт"}</button>
      </div>
    </section>
    <section className="company-grid">
      {groups.map(([group, items]) => <article className="card company-card" key={group}><div className="company-card-head"><div><span className="eyebrow">КОМПАНИЯ</span><h2>{group}</h2></div><b>{items.length} контакт(а)</b></div>{items.map((contact) => <div className="company-contact" key={contact.id}><div><strong>{contact.name}</strong><a href={`mailto:${contact.email}`}>{contact.email}</a><small>{contact.contract_id ? "Связан с договором" : "Без договора"} · {contact.source === "gmail" ? "найден во входящих" : "добавлен вручную"}</small>{!contact.confirmed && <small>Предложение: отправитель письма · требуется проверка человеком</small>}{contact.company_activity && <p><b>О чём переписка:</b> {contact.company_activity}</p>}</div><div>{!contact.confirmed && <button className="secondary" disabled={busy} onClick={() => confirmContact(contact)}>Подтвердить проект</button>}<button disabled={busy || !contact.confirmed} onClick={() => prepareEmail(contact)}>Подготовить письмо</button></div></div>)}</article>)}
      {!groups.length && <div className="card empty"><Users /><p>Контактов пока нет. Получите письма из Gmail или добавьте клиента выше.</p></div>}
    </section>
    {!!clientDrafts.length && <section className="card company-drafts"><h2>Исходящие клиентам</h2><p>Отправка возможна только после отдельного подтверждения.</p>{clientDrafts.map((draft) => <article key={draft.id}><div><strong>{draft.subject}</strong><small>{draft.recipient_to} · {draft.status}</small><p>{draft.body}</p></div><div>{draft.status === "draft" && <><button className="secondary" onClick={() => onUpdateDraft(draft, "rejected")}>Отклонить</button><button onClick={() => onUpdateDraft(draft, "approved")}>Подтвердить</button></>}{draft.status === "approved" && <button onClick={() => onSendDraft(draft)}>Отправить через Gmail</button>}{draft.status === "sent" && <span className="draft-status ready">Отправлено</span>}</div></article>)}</section>}
  </section>;
}
