import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { EvidenceFragmentCard } from "./EvidenceFragmentCard";
import { EVIDENCE_FRAGMENT_SCHEMA_VERSION } from "./evidenceReadModel";

export type EvidenceRef = { id: string; revision: number };

type Props = {
  active: boolean;
  evidenceRefs: EvidenceRef[];
};

const UNAVAILABLE = {
  schema_version: EVIDENCE_FRAGMENT_SCHEMA_VERSION,
  state: "unavailable",
  status: "unavailable",
  reason_code: "resource_unavailable",
} as const;

export function EvidencePanel({ active, evidenceRefs }: Props) {
  const [items, setItems] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(false);
  const key = evidenceRefs.map(({ id, revision }) => `${id}:${revision}`).join("|");

  useEffect(() => {
    const controller = new AbortController();
    if (!active || evidenceRefs.length === 0) {
      setItems([]);
      setLoading(false);
      return () => controller.abort();
    }
    setItems([]);
    setLoading(true);
    Promise.all(evidenceRefs.map(({ id, revision }) =>
      api<unknown>(`/api/v54/evidence/${encodeURIComponent(id)}/fragment?revision=${revision}`, {
        method: "GET",
        cache: "no-store",
        signal: controller.signal,
      }).catch(() => UNAVAILABLE),
    )).then((results) => {
      if (!controller.signal.aborted) setItems(results);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => {
      controller.abort();
      setItems([]);
    };
  // The serialized exact refs deliberately define the read identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, key]);

  if (!active || evidenceRefs.length === 0) return null;
  return <section className="inbox-evidence" aria-label="Доказательства">
    <h3>Доказательства и точные версии</h3>
    {loading && <p role="status">Проверяем доступ к доказательствам…</p>}
    {!loading && items.map((item, index) => <EvidenceFragmentCard input={item} key={`${key}:${index}`} />)}
  </section>;
}
