export type ContractDocumentTab = "recommended" | "server" | "upload" | "google";

type DocumentItem = { id: number; name: string; source: string };
type Candidate = { document_id: number; name: string; score: number; reasons: string[] };

type Props = {
  contractId: number;
  sourceDocumentId?: number;
  open: boolean;
  busy: boolean;
  tab: ContractDocumentTab;
  query: string;
  documents: DocumentItem[];
  candidates: Candidate[];
  onToggle: () => void;
  onSuggest: () => void;
  onTabChange: (tab: ContractDocumentTab) => void;
  onQueryChange: (query: string) => void;
  onLink: (documentId: number) => void;
};

const tabs: [ContractDocumentTab, string][] = [
  ["recommended", "Рекомендованные"], ["server", "Сервер / реестр"],
  ["upload", "Облако / загрузки"], ["google", "Google Drive"],
];

export function ContractDocumentPicker(props: Props) {
  const visible = props.documents.filter((document) => {
    const source = (document.source || "").toLowerCase();
    const candidate = props.candidates.find((row) => row.document_id === document.id);
    const sourceMatches = props.tab === "recommended" ? Boolean(candidate?.score)
      : props.tab === "server" ? true : props.tab === "upload" ? !source.includes("google") : source.includes("google");
    const search = props.query.trim().toLocaleLowerCase("ru-RU");
    return sourceMatches && (!search || document.name.toLocaleLowerCase("ru-RU").includes(search));
  }).sort((left, right) => {
    const leftScore = props.candidates.find((row) => row.document_id === left.id)?.score || 0;
    const rightScore = props.candidates.find((row) => row.document_id === right.id)?.score || 0;
    return rightScore - leftScore || right.id - left.id;
  });

  return <>
    <label>1. Документ-источник</label>
    <button type="button" data-ai-help="Выбрать существующий файл договора из каталога документов проекта" onClick={props.onToggle}>
      {props.open ? "Скрыть каталог документов" : "Выбрать файл из каталога"}
    </button>
    <button type="button" className="secondary" disabled={props.busy} onClick={props.onSuggest}>
      {props.busy ? "Анализирую реестр…" : "Найти договор по номеру, контрагенту и тексту"}
    </button>
    {props.open && <div className="contract-catalog-picker">
      <div className="contract-source-tabs">{tabs.map(([source, title]) =>
        <button type="button" className={props.tab === source ? "selected" : "secondary"} onClick={() => props.onTabChange(source)} key={source}>{title}</button>,
      )}</div>
      <input value={props.query} onChange={(event) => props.onQueryChange(event.target.value)} placeholder="Поиск документа по названию" />
      <select value={props.sourceDocumentId || 0} onChange={(event) => props.onLink(Number(event.target.value))}>
        <option value={0}>Выберите документ договора</option>
        {visible.map((document) => {
          const score = props.candidates.find((row) => row.document_id === document.id)?.score;
          return <option value={document.id} key={document.id}>{score ? `${score}% · ` : ""}{document.name}</option>;
        })}
      </select>
      <small>Выберите файл в списке — связь сохранится сразу. Сам файл и его название не изменяются.</small>
    </div>}
    {props.tab === "recommended" && !props.candidates.length && <small>Нажмите «Найти договор…»: система проверит не только имя файла, но и извлечённый текст.</small>}
    {props.candidates.slice(0, 3).map((candidate) => <div className="contract-candidate" key={candidate.document_id}>
      <span><strong>{candidate.score}% · {candidate.name}</strong><small>{candidate.reasons.join("; ") || "слабое совпадение"}</small></span>
      <button type="button" className="secondary" disabled={props.sourceDocumentId === candidate.document_id} onClick={() => props.onLink(candidate.document_id)}>
        {props.sourceDocumentId === candidate.document_id ? "Привязан" : "Привязать"}
      </button>
    </div>)}
    <small>«Рекомендованные» ранжируются по реквизитам и тексту; «Сервер / реестр» показывает все документы проекта; «Облако / загрузки» — загруженные файлы; «Google Drive» — документы, проиндексированные из подключённого Диска.</small>
  </>;
}
