import { AlertTriangle, CheckCircle2, FileQuestion, Files, PencilLine } from "lucide-react";
import "./folder-analysis.css";

export type FolderAnalysisAction = {
  source: string;
  proposed_name: string;
  target_folder: string;
  confidence: number;
  special_case?: string;
};

export type FolderAnalysisProposal = {
  id: number;
  status: string;
  folder_name: string;
  actions: FolderAnalysisAction[];
};

type Props = {
  proposals: FolderAnalysisProposal[];
  onOpenDocuments: () => void;
};

export function FolderAnalysisSummary({ proposals, onOpenDocuments }: Props) {
  const actions = proposals.flatMap((proposal) => proposal.actions);
  if (!actions.length) return null;

  const recognized = actions.filter(
    (action) => action.confidence >= 0.75 && !action.special_case,
  ).length;
  const needsReview = actions.filter(
    (action) => action.confidence < 0.75 || Boolean(action.special_case),
  ).length;
  const unrecognized = actions.filter((action) =>
    action.target_folder.toUpperCase().includes("НЕРАЗОБРАННОЕ"),
  ).length;
  const renames = actions.filter(
    (action) => action.source.trim() !== action.proposed_name.trim(),
  ).length;
  const waiting = proposals.filter((proposal) =>
    ["waiting_confirmation", "approved", "ready_to_apply_to_copy"].includes(
      proposal.status,
    ),
  ).length;

  return (
    <section className="card folder-analysis-summary" aria-label="Итог анализа папки">
      <div className="folder-analysis-heading">
        <div>
          <span className="eyebrow">Итог анализа рабочей папки</span>
          <h2>Система разобрала {actions.length} файлов</h2>
          <p>
            Оригиналы не изменены. Проверьте спорные позиции и примените
            подтверждённые изменения только к безопасной копии.
          </p>
        </div>
        <button onClick={onOpenDocuments}>Открыть реестр документов</button>
      </div>
      <div className="folder-analysis-metrics">
        <article><Files /><span>Всего</span><strong>{actions.length}</strong></article>
        <article><CheckCircle2 /><span>Распознано</span><strong>{recognized}</strong></article>
        <article className={needsReview ? "warn" : ""}><AlertTriangle /><span>Проверить</span><strong>{needsReview}</strong></article>
        <article className={unrecognized ? "warn" : ""}><FileQuestion /><span>Неразобранное</span><strong>{unrecognized}</strong></article>
        <article><PencilLine /><span>Переименований</span><strong>{renames}</strong></article>
      </div>
      <div className="folder-analysis-next">
        {waiting
          ? `Следующий шаг: проверьте ${waiting} пакет(а) ниже и подтвердите безопасные действия.`
          : "Анализ обработан. Документы доступны в реестре проекта."}
      </div>
    </section>
  );
}
