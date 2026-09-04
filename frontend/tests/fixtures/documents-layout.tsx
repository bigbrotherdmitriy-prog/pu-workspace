import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  DocumentsModule,
  type DocumentCard,
  type DocumentListItem,
} from "../../src/modules/documents/DocumentsModule";
import "../../src/styles.css";
import "../../src/source.css";
import "../../src/brand.css";
import "../../src/metric-actions.css";
import "../../src/designer.css";

const names = [
  "Приложение №3 График производства работ и поставки оборудования.docx",
  "Приложение №6.docx",
  "Акт выполненных работ.pdf",
  "Скан без названия.pdf",
  "Реестр исполнительной документации.xlsx",
];
const sources = ["google_drive_copy", "local_upload", "gmail"];

const documents: DocumentListItem[] = Array.from({ length: 462 }, (_, index) => {
  const id = index + 1;
  const failed = id % 17 === 0;
  const waiting = !failed && id % 5 === 0;
  return {
    id,
    name: id === 321 ? "Уникальный план-график № 321.xlsx" : names[index % names.length],
    source: sources[index % sources.length],
    status: failed ? "failed" : waiting ? "discovered" : "analyzed",
    current_version: id % 11 === 0 ? 3 : 1,
    mime_type: index % 5 === 4
      ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      : index % 5 < 2
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : "application/pdf",
    summary: id === 321
      ? "Контрольная сводка для проверки поиска по содержимому."
      : `Синтетическая сводка документа № ${id} для проверки плотного реестра.`,
    source_modified_at: `2026-08-${String((id % 28) + 1).padStart(2, "0")}T12:00:00Z`,
    extraction_method: waiting ? undefined : "text_and_ocr",
    extraction_quality: failed ? "low" : "high",
    ocr_pages: (id % 18) + 1,
    ocr_reprocess_available: index % 4 !== 0,
  };
});

function detail(item: DocumentListItem): DocumentCard {
  return {
    ...item,
    source_url: "https://example.invalid/synthetic-document",
    summary: `${item.summary}\n\nСодержимое здесь намеренно длиннее одной строки: оно проверяет перенос текста и устойчивость правой панели на узких экранах без использования реальных документов.`,
    versions: [{ version: 1, created_at: "2026-08-01T12:00:00Z" }],
    links: { tasks: 5, risks: 2, decisions: 3, drafts: 1 },
  };
}

function Fixture() {
  const [selected, setSelected] = useState<DocumentCard>(() => detail(documents[0]));
  return <div className="shell">
    <aside aria-hidden="true">[PU] Synthetic</aside>
    <main>
      <DocumentsModule
        collapsed={false}
        knowledgeMode={false}
        documents={documents}
        selected={selected}
        onSelect={(item) => setSelected(detail(item))}
        projectId={1}
        onOcrComplete={() => {}}
      />
    </main>
  </div>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
