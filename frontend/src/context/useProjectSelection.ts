import { useRef, useState } from "react";

export function requestedProjectId(projects: { id: number }[], requested: number): number {
  if (requested > 0) {
    if (!projects.some(project => project.id === requested)) {
      throw new Error(`Проект №${requested} отсутствует в ответе сервера. Повторите загрузку или выберите проект явно.`);
    }
    return requested;
  }
  return projects[0]?.id || 0;
}

export function restoredProjectId(): number {
  const callbackProjectId = Number(new URLSearchParams(window.location.search).get("project_id"));
  if (Number.isSafeInteger(callbackProjectId) && callbackProjectId > 0) return callbackProjectId;
  const storedProjectId = Number(sessionStorage.getItem("pu_active_project_id"));
  return Number.isSafeInteger(storedProjectId) && storedProjectId > 0 ? storedProjectId : 0;
}


export function useProjectSelection() {
  const initialProjectId = restoredProjectId();
  const [projectId, setProjectId] = useState(initialProjectId);
  const projectIdRef = useRef(projectId);

  function rememberProject(id: number) {
    projectIdRef.current = id;
    setProjectId(id);
    sessionStorage.setItem("pu_active_project_id", String(id));
  }

  return { projectId, projectIdRef, rememberProject, initialProjectId };
}
