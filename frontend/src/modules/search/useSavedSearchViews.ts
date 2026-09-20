import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { isVersionConflict, type ProjectSearchFilters } from "./useProjectSearch";

export type SavedSearchView = {
  id: number;
  project_id: number;
  name: string;
  filters: {
    q?: string | null;
    types?: string[];
    date_from?: string | null;
    date_to?: string | null;
    contract_id?: number | null;
    counterparty?: string | null;
  };
  record_version: number;
};

export type SearchViewDraft = { query: string; filters: ProjectSearchFilters };

export function savedViewPayload(projectId: number, name: string, draft: SearchViewDraft) {
  return {
    project_id: projectId,
    name: name.trim(),
    filters: {
      q: draft.query.trim() || null,
      types: draft.filters.types,
      date_from: draft.filters.dateFrom || null,
      date_to: draft.filters.dateTo || null,
      contract_id: draft.filters.contractId ? Number(draft.filters.contractId) : null,
      counterparty: draft.filters.counterparty.trim() || null,
    },
  };
}

export function draftFromSavedView(view: SavedSearchView): SearchViewDraft {
  const value = view.filters || {};
  return {
    query: typeof value.q === "string" ? value.q : "",
    filters: {
      types: Array.isArray(value.types) ? value.types.filter((item): item is ProjectSearchFilters["types"][number] => (
        ["project", "document", "contract", "task", "obligation", "risk", "decision", "message"] as string[]
      ).includes(item)) : [],
      dateFrom: typeof value.date_from === "string" ? value.date_from : "",
      dateTo: typeof value.date_to === "string" ? value.date_to : "",
      contractId: Number.isSafeInteger(value.contract_id) && Number(value.contract_id) > 0 ? String(value.contract_id) : "",
      counterparty: typeof value.counterparty === "string" ? value.counterparty : "",
    },
  };
}

export function useSavedSearchViews(projectId: number, enabled = true) {
  const [views, setViews] = useState<SavedSearchView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);

  const reload = useCallback(async () => {
    const requestSequence = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const response = await api<{ views: SavedSearchView[] }>(`/saved-search-views?project_id=${projectId}`);
      if (requestSequence === sequence.current) setViews(Array.isArray(response.views) ? response.views : []);
    } catch (caught) {
      if (requestSequence === sequence.current) {
        setViews([]);
        setError(caught instanceof Error ? caught.message : "Не удалось загрузить сохранённые виды");
      }
    } finally {
      if (requestSequence === sequence.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    ++sequence.current;
    setViews([]);
    setError("");
    if (projectId && enabled) void reload();
  }, [projectId, enabled, reload]);

  async function create(name: string, draft: SearchViewDraft) {
    const created = await api<SavedSearchView>("/saved-search-views", {
      method: "POST",
      body: JSON.stringify(savedViewPayload(projectId, name, draft)),
    });
    setViews((current) => [...current, created].sort((left, right) => left.name.localeCompare(right.name, "ru")));
    return created;
  }

  async function rename(view: SavedSearchView, name: string) {
    try {
      const updated = await api<SavedSearchView>(`/saved-search-views/${view.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          expected_record_version: view.record_version,
        }),
      });
      setViews((current) => current.map((item) => item.id === updated.id ? updated : item));
      return updated;
    } catch (caught) {
      if (isVersionConflict(caught)) {
        await reload();
        throw new Error("Сохранённый вид уже изменён. Список обновлён — повторите действие.");
      }
      throw caught;
    }
  }

  async function remove(view: SavedSearchView) {
    try {
      await api(`/saved-search-views/${view.id}?expected_record_version=${view.record_version}`, { method: "DELETE" });
      setViews((current) => current.filter((item) => item.id !== view.id));
    } catch (caught) {
      if (isVersionConflict(caught)) {
        await reload();
        throw new Error("Сохранённый вид уже изменён. Список обновлён — повторите действие.");
      }
      throw caught;
    }
  }

  return { views: views.filter((view) => view.project_id === projectId), loading, error, create, rename, remove, reload };
}
