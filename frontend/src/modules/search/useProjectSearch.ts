import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ProjectSearchHit } from "./ProjectSearchResults";

const SEARCH_DELAY_MS = 250;
const RESULT_LIMIT = 30;
const RESULT_TYPES = new Set<ProjectSearchHit["kind"]>([
  "project", "document", "contract", "task", "obligation", "risk", "decision", "message",
]);

type SearchItem = {
  entity_type: ProjectSearchHit["kind"];
  entity_id: number;
  name: string;
  date: string | null;
  project: { id: number; name: string };
  contract: { id: number; number: string; title: string } | null;
  counterparty: string | null;
  status: string | null;
  links: Array<{ relation: string; href: string; revision?: number }>;
};

type SearchResponse = {
  items: SearchItem[];
  next_cursor: string | null;
  limit: number;
  scan_truncated: boolean;
  scan_cap_per_type: number;
  external_actions_created: false;
};

export function searchPath(projectId: number, query: string): string {
  const params = new URLSearchParams({ query: query.trim(), limit: String(RESULT_LIMIT) });
  return `/api/search/projects/${projectId}?${params.toString()}`;
}

export function toSearchHits(value: SearchResponse): ProjectSearchHit[] {
  if (!value || !Array.isArray(value.items) || value.external_actions_created !== false) return [];
  return value.items.flatMap((item) => {
    if (!item || !RESULT_TYPES.has(item.entity_type)
      || !Number.isSafeInteger(item.entity_id) || item.entity_id < 1
      || typeof item.name !== "string" || !Array.isArray(item.links)) return [];
    const detail = [item.contract?.number, item.counterparty, item.status, item.date]
      .filter((part): part is string => typeof part === "string" && part.length > 0)
      .join(" · ");
    return [{ id: item.entity_id, kind: item.entity_type, title: item.name, detail }];
  });
}

export function useProjectSearch(projectId: number, query: string) {
  const [hits, setHits] = useState<ProjectSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const requestSequence = useRef(0);

  useEffect(() => {
    const normalized = query.trim();
    const sequence = ++requestSequence.current;
    if (!projectId || normalized.length < 2) {
      setHits([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await api<SearchResponse>(searchPath(projectId, normalized));
        if (sequence === requestSequence.current) setHits(toSearchHits(result));
      } catch {
        if (sequence === requestSequence.current) setHits([]);
      } finally {
        if (sequence === requestSequence.current) setLoading(false);
      }
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [projectId, query]);

  return { hits, loading };
}
