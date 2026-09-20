import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";

export const SEARCH_KINDS = [
  "project", "document", "contract", "task", "obligation", "risk", "decision", "message",
] as const;

export type ProjectSearchKind = typeof SEARCH_KINDS[number];

export type ProjectSearchFilters = {
  types: ProjectSearchKind[];
  dateFrom: string;
  dateTo: string;
  contractId: string;
  counterparty: string;
};

export const EMPTY_SEARCH_FILTERS: ProjectSearchFilters = {
  types: [], dateFrom: "", dateTo: "", contractId: "", counterparty: "",
};

export type ProjectSearchHit = {
  id: number;
  kind: ProjectSearchKind;
  title: string;
  detail: string;
  navigation: {
    section: string;
    project_id: number;
    entity_type: ProjectSearchKind;
    entity_id: number;
  };
};

type SearchItem = {
  entity_type: ProjectSearchKind;
  entity_id: number;
  name: string;
  date: string | null;
  project_id: number;
  contract_id: number | null;
  counterparty: string | null;
  status: string | null;
  navigation: ProjectSearchHit["navigation"];
};

type SearchResponse = {
  items: SearchItem[];
  next_cursor: string | null;
  limit: number;
  scan_truncated: boolean;
  scan_cap_per_type: number;
  external_actions_created: false;
};

const KIND_SET = new Set<string>(SEARCH_KINDS);
const SEARCH_DELAY_MS = 250;
const RESULT_LIMIT = 50;

export function hasSearchCriteria(query: string, filters: ProjectSearchFilters) {
  return query.trim().length >= 2 || filters.types.length > 0 || Boolean(
    filters.dateFrom || filters.dateTo || filters.contractId || filters.counterparty.trim(),
  );
}

export function projectSearchPath(
  projectId: number,
  query: string,
  filters: ProjectSearchFilters,
  cursor?: string | null,
) {
  const params = new URLSearchParams({ project_id: String(projectId), limit: String(RESULT_LIMIT) });
  if (query.trim()) params.set("q", query.trim());
  if (filters.types.length) params.set("types", filters.types.join(","));
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  if (filters.contractId) params.set("contract_id", filters.contractId);
  if (filters.counterparty.trim()) params.set("counterparty", filters.counterparty.trim());
  if (cursor) params.set("cursor", cursor);
  return `/project-search?${params.toString()}`;
}

export function toSearchHits(value: SearchResponse, projectId: number): ProjectSearchHit[] {
  if (!value || !Array.isArray(value.items) || value.external_actions_created !== false) return [];
  return value.items.flatMap((item) => {
    const navigation = item?.navigation;
    if (!item || !KIND_SET.has(item.entity_type) || !Number.isSafeInteger(item.entity_id) || item.entity_id < 1
      || item.project_id !== projectId || typeof item.name !== "string" || !navigation
      || navigation.project_id !== projectId || navigation.entity_id !== item.entity_id
      || navigation.entity_type !== item.entity_type || typeof navigation.section !== "string") return [];
    const detail = [item.contract_id ? `Договор №${item.contract_id}` : null, item.counterparty, item.status, item.date]
      .filter((part): part is string => typeof part === "string" && part.length > 0)
      .join(" · ");
    return [{ id: item.entity_id, kind: item.entity_type, title: item.name, detail, navigation }];
  });
}

export function useProjectSearch(projectId: number, query: string, filters: ProjectSearchFilters) {
  const [hits, setHits] = useState<ProjectSearchHit[]>([]);
  const [resultProjectId, setResultProjectId] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [scanTruncated, setScanTruncated] = useState(false);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const filterKey = useMemo(() => JSON.stringify(filters), [filters]);

  const request = useCallback(async (cursor: string | null, append: boolean) => {
    const requestSequence = ++sequence.current;
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setLoading(true);
    setError("");
    try {
      const response = await api<SearchResponse>(projectSearchPath(projectId, query, filters, cursor), {
        signal: nextController.signal,
      });
      if (requestSequence !== sequence.current || nextController.signal.aborted) return;
      const mapped = toSearchHits(response, projectId);
      setHits((current) => append ? [...current, ...mapped] : mapped);
      setResultProjectId(projectId);
      setNextCursor(typeof response.next_cursor === "string" ? response.next_cursor : null);
      setScanTruncated((current) => append ? current || response.scan_truncated === true : response.scan_truncated === true);
    } catch (caught) {
      if (requestSequence !== sequence.current || nextController.signal.aborted) return;
      if (!append) {
        setHits([]);
        setResultProjectId(0);
      }
      setNextCursor(null);
      setScanTruncated(false);
      setError(caught instanceof Error ? caught.message : "Поиск временно недоступен");
    } finally {
      if (requestSequence === sequence.current) setLoading(false);
    }
  }, [filters, projectId, query]);

  useEffect(() => {
    ++sequence.current;
    controller.current?.abort();
    setHits([]);
    setResultProjectId(0);
    setNextCursor(null);
    setScanTruncated(false);
    setError("");
    setLoading(false);
    if (!projectId || !hasSearchCriteria(query, filters)) return;
    const timer = window.setTimeout(() => void request(null, false), SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.current?.abort();
    };
  }, [projectId, query, filterKey, request]);

  return {
    hits: resultProjectId === projectId ? hits : [], loading, error, nextCursor, scanTruncated,
    loadMore: () => nextCursor && !loading ? request(nextCursor, true) : Promise.resolve(),
  };
}

export function isVersionConflict(error: unknown) {
  return error instanceof ApiError && error.status === 409;
}
