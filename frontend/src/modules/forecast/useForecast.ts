import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { parseForecastReport, type ForecastReport } from "./types";

export type ForecastLoadState = "idle" | "loading" | "ready" | "error";

export function useForecast(projectId: number | null, enabled = true) {
  const requestSequence = useRef(0);
  const [report, setReport] = useState<ForecastReport | null>(null);
  const [state, setState] = useState<ForecastLoadState>("idle");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const sequence = ++requestSequence.current;
    if (!enabled || !projectId) {
      setReport(null);
      setState("idle");
      setError(null);
      return;
    }
    setState("loading");
    setError(null);
    try {
      const next = parseForecastReport(await api<unknown>(`/execution/forecast/${projectId}`));
      if (sequence !== requestSequence.current) return;
      setReport(next);
      setState("ready");
    } catch (caught) {
      if (sequence !== requestSequence.current) return;
      setReport(null);
      setState("error");
      setError(caught instanceof Error ? caught.message : "Не удалось построить прогноз");
    }
  }, [enabled, projectId]);

  useEffect(() => { void reload(); }, [reload]);

  return { report, state, error, reload };
}
