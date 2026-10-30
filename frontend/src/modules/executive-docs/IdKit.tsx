import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";

type Item = {
  key: string; title: string; doc_kind: string; section: string | null; norm_ref: string | null;
  reason: string; source: string; checked: boolean;
};
type Proposal = { object_name: string | null; sections: string[]; rd_items: number; estimate_lines: number; items: Item[] };

const KIND_OPTIONS: [string, string][] = [
  ["aosr", "АОСР"], ["act", "Акт"], ["protocol", "Протокол"], ["journal", "Журнал"],
  ["certificate", "Сертификат/паспорт"], ["other", "Другое"],
];

export function IdKit({ projectId, objectNames, onSaved }: { projectId: number; objectNames: string[]; onSaved?: () => void }) {
  const [objectName, setObjectName] = useState<string>(objectNames[0] ?? "");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [own, setOwn] = useState<Item[]>([]);
  const [ownTitle, setOwnTitle] = useState("");
  const [ownKind, setOwnKind] = useState("other");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const q = objectName ? `?object_name=${encodeURIComponent(objectName)}` : "";
      const data = await api<Proposal>(`/projects/${projectId}/executive-docs/kit/proposal${q}`);
      setProposal(data);
      setChecked(Object.fromEntries(data.items.map((i) => [i.key, i.checked])));
      setOwn([]);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [projectId, objectName]);

  useEffect(() => { void load(); }, [load]);

  const all = useMemo(() => [...(proposal?.items ?? []), ...own], [proposal, own]);
  const selected = all.filter((i) => checked[i.key]);
  const groups = useMemo(() => {
    const map = new Map<string, Item[]>();
    for (const i of all) {
      const g = i.section || "Прочее";
      map.set(g, [...(map.get(g) ?? []), i]);
    }
    return [...map.entries()];
  }, [all]);

  const addOwn = () => {
    const title = ownTitle.trim();
    if (!title || all.some((i) => i.title.toLowerCase() === title.toLowerCase())) return;
    const item: Item = { key: `own:${title.toLowerCase()}`, title, doc_kind: ownKind, section: "Добавлено вручную", norm_ref: null, reason: "добавлено вами", source: "own", checked: true };
    setOwn((o) => [...o, item]);
    setChecked((c) => ({ ...c, [item.key]: true }));
    setOwnTitle("");
  };

  const save = async () => {
    setBusy(true);
    try {
      const res = await api<{ created: number }>(`/projects/${projectId}/executive-docs/kit`, {
        method: "POST",
        body: JSON.stringify({
          object_name: objectName || null,
          items: selected.map((i) => ({ title: i.title, doc_kind: i.doc_kind, section: i.section === "Добавлено вручную" ? null : i.section, norm_ref: i.norm_ref, reason: i.reason })),
        }),
      });
      setNote(`Комплект создан: ${res.created} документов. Готовность пересчитана на вкладке «Готовность».`);
      setError("");
      onSaved?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const setGroup = (items: Item[], value: boolean) =>
    setChecked((c) => ({ ...c, ...Object.fromEntries(items.map((i) => [i.key, value])) }));

  return (
    <section className="id-object">
      <h3>Состав комплекта ИД</h3>
      <p className="id-hint">
        Секретарь просмотрел РД и сметы проекта и предлагает список исполнительной документации. Снимите галочку с лишнего,
        добавьте своё и нажмите «Создать комплект».
      </p>
      {objectNames.length > 1 && (
        <label>Объект{" "}
          <select value={objectName} onChange={(e) => setObjectName(e.target.value)}>
            {objectNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      )}
      {error && <p className="id-error" role="alert">{error}</p>}
      {proposal && (
        <p className="id-hint">
          Учтено: позиций РД — {proposal.rd_items}, строк смет — {proposal.estimate_lines}
          {proposal.sections.length ? `, разделы: ${proposal.sections.join(", ")}` : ""}.
          {proposal.rd_items === 0 && proposal.estimate_lines === 0 ? " РД и смет пока нет, поэтому предложены только обязательные документы." : ""}
        </p>
      )}
      {groups.map(([section, items]) => (
        <fieldset key={section} className="id-kit-group">
          <legend>
            {section}{" "}
            <button type="button" className="link-button" onClick={() => setGroup(items, true)}>все</button>{" / "}
            <button type="button" className="link-button" onClick={() => setGroup(items, false)}>никого</button>
          </legend>
          {items.map((i) => (
            <label key={i.key} className="id-kit-item">
              <input type="checkbox" checked={!!checked[i.key]} onChange={(e) => setChecked((c) => ({ ...c, [i.key]: e.target.checked }))} />
              <span>
                {i.title}
                <small className="id-hint"> — {i.reason}{i.norm_ref ? ` (${i.norm_ref})` : ""}</small>
              </span>
            </label>
          ))}
        </fieldset>
      ))}
      <div className="id-kit-add">
        <input value={ownTitle} placeholder="Свой документ, например: Акт на ограждение" onChange={(e) => setOwnTitle(e.target.value)} />
        <select value={ownKind} onChange={(e) => setOwnKind(e.target.value)} aria-label="Вид документа">
          {KIND_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <button type="button" onClick={addOwn} disabled={!ownTitle.trim()}>Добавить</button>
      </div>
      <div className="id-kit-actions">
        <button type="button" onClick={save} disabled={busy || selected.length === 0}>
          Создать комплект ({selected.length})
        </button>
        <button type="button" onClick={() => void load()} disabled={busy}>Предложить заново</button>
      </div>
      {note && <p className="id-ok" role="status">{note}</p>}
    </section>
  );
}
