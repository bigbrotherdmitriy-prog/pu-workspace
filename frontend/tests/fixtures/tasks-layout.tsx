import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { TasksModule } from "../../src/modules/tasks/TasksModule";
import { ContextualAssistant } from "../../src/modules/ai-secretary/ContextualAssistant";
import { task, members, documents, history } from "./tasks";
import "../../src/styles.css";
import "../../src/source.css";
import "../../src/brand.css";
import "../../src/metric-actions.css";
import "../../src/designer.css";

function Fixture() {
  const [filter, setFilter] = useState("all");
  const [completion, setCompletion] = useState(0);
  const [historyId, setHistoryId] = useState(0);
  const [note, setNote] = useState("");
  const [documentId, setDocumentId] = useState(0);
  const [helpCount, setHelpCount] = useState(0);
  const [helpPrompt, setHelpPrompt] = useState("");
  return <div className="shell"><aside aria-hidden="true">[PU] Synthetic</aside><main><div className="content">
    <TasksModule tasks={[task]} members={members} documents={documents} filter={filter} onFilterChange={setFilter}
      completionTaskId={completion} completionNote={note} completionDocumentId={documentId}
      historyTaskId={historyId} history={history} onAssign={() => {}} onApproveExternal={() => {}} onUpdate={() => {}}
      onStartCompletion={(item) => setCompletion(item.id)} onCancelCompletion={() => setCompletion(0)}
      onCompletionNoteChange={setNote} onCompletionDocumentChange={setDocumentId}
      onLoadHistory={(item) => setHistoryId(item.id)} />
    <output aria-label="Число запросов помощнику">{helpCount}</output>
    <output aria-label="Запрос помощнику" style={{ display: "block", overflowWrap: "anywhere" }}>{helpPrompt}</output>
  </div></main><ContextualAssistant section="Задачи" onAsk={(prompt) => { setHelpCount((count) => count + 1); setHelpPrompt(prompt); }} /></div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
