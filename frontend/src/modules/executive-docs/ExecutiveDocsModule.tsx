import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { EquipmentTestAct } from "./EquipmentTestAct";
import { IdOrders } from "./IdOrders";
import { InsulationProtocol } from "./InsulationProtocol";
import { EARTHING_PROTOCOL, PHASE_ZERO_PROTOCOL, ProtocolForm } from "./ProtocolForm";
import "./executive-docs.css";

type FileIssue = { path: string; issues: string[] };
type ObjectReport = {
  object_name: string;
  percent: number;
  required_total: number;
  present: number;
  draft: number;
  missing: string[];
  recommended_missing: string[];
  file_issues: FileIssue[];
  empty_folders: string[];
  duplicates: [string, string[]][];
  last_scan_at: string | null;
};
type ReportResponse = { objects: ObjectReport[] };
type Source = { id: number; object_name: string; folder_name: string; last_scan_at: string | null };
type Participant = {
  id: number;
  object_name: string | null;
  role: string;
  position: string | null;
  full_name: string;
  order_ref: string | null;
  nrs_id: string | null;
  org_name: string | null;
};

const ROLES: { value: string; label: string }[] = [
  { value: "customer_control", label: "Представитель заказчика (стройконтроль)" },
  { value: "builder", label: "Представитель подрядчика" },
  { value: "builder_control", label: "Представитель подрядчика (стройконтроль)" },
  { value: "designer", label: "Представитель проектировщика" },
  { value: "performer", label: "Представитель исполнителя работ" },
];

const ROLE_LABELS: Record<string, string> = Object.fromEntries(ROLES.map((r) => [r.value, r.label]));

const ISSUE_LABELS: Record<string, string> = {
  service_file: "служебный файл",
  draft_name: "черновик в названии",
  editable_format_not_signed: "Word, не подписан",
  blank_fields: "незаполненные поля",
  unreadable: "не читается",
};

function formatDate(iso: string | null): string {
  if (!iso) return "не проверялось";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("ru-RU");
}

function cookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

export function ExecutiveDocsModule({ projectId }: { projectId: number }) {
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [uploadNote, setUploadNote] = useState<Record<string, string>>({});

  const [newObjectName, setNewObjectName] = useState("");
  const [newFolderId, setNewFolderId] = useState("");

  const [pRole, setPRole] = useState(ROLES[0].value);
  const [pFullName, setPFullName] = useState("");
  const [pPosition, setPPosition] = useState("");
  const [pOrderRef, setPOrderRef] = useState("");
  const [pNrsId, setPNrsId] = useState("");

  const [aContractId, setAContractId] = useState("");
  const [aObjectName, setAObjectName] = useState("");
  const [aNumber, setANumber] = useState("");
  const [aWorks, setAWorks] = useState("");
  const [aDesignDocs, setADesignDocs] = useState("");
  const [aMaterials, setAMaterials] = useState("");
  const [aWorkStart, setAWorkStart] = useState("");
  const [aWorkFinish, setAWorkFinish] = useState("");
  const [aNorms, setANorms] = useState("");
  const [aNextWorks, setANextWorks] = useState("");
  const [aosrMissing, setAosrMissing] = useState<string[] | null>(null);
  const [aosrOk, setAosrOk] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, s, p] = await Promise.all([
        api<ReportResponse>(`/projects/${projectId}/executive-docs/report`),
        api<Source[]>(`/projects/${projectId}/executive-docs/sources`),
        api<Participant[]>(`/projects/${projectId}/executive-docs/participants`),
      ]);
      setReport(r);
      setSources(s);
      setParticipants(p);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const sourceFor = (objectName: string): Source | undefined =>
    sources.find((s) => s.object_name === objectName);

  const connectFolder = async () => {
    if (!newObjectName.trim() || !newFolderId.trim()) return;
    try {
      await api(`/projects/${projectId}/executive-docs/sources`, {
        method: "POST",
        body: JSON.stringify({ object_name: newObjectName, folder_id: newFolderId }),
      });
      await api(`/projects/${projectId}/executive-docs/requirements/build`, {
        method: "POST",
        body: JSON.stringify({ object_name: newObjectName, sections: ["ЭМ", "ОВиК", "АСДУ", "АР"] }),
      });
      setNewObjectName("");
      setNewFolderId("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const scan = async (objectName: string, sourceId: number) => {
    setBusy((b) => ({ ...b, [objectName]: true }));
    try {
      await api(`/projects/${projectId}/executive-docs/sources/${sourceId}/scan`, { method: "POST" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy((b) => ({ ...b, [objectName]: false }));
    }
  };

  const uploadFiles = async (objectName: string, sourceId: number, files: File[]) => {
    if (!files.length) return;
    setBusy((b) => ({ ...b, [objectName]: true }));
    setUploadNote((n) => ({ ...n, [objectName]: "" }));
    const failed: string[] = [];
    for (const file of files) {
      try {
        const response = await fetch(
          `/projects/${projectId}/executive-docs/sources/${sourceId}/upload?name=${encodeURIComponent(file.name)}`,
          { method: "POST", body: file, credentials: "same-origin" },
        );
        if (!response.ok) failed.push(`${file.name} (${response.status === 413 ? "слишком большой" : `ошибка ${response.status}`})`);
      } catch {
        failed.push(`${file.name} (не удалось загрузить)`);
      }
    }
    setUploadNote((n) => ({
      ...n,
      [objectName]: failed.length
        ? `Не загружено: ${failed.join(", ")}`
        : `Загружено файлов: ${files.length}. Готовность пересчитана.`,
    }));
    try { await load(); } catch { /* the error banner already covers it */ }
    setBusy((b) => ({ ...b, [objectName]: false }));
  };

  const downloadCableJournal = async () => {
    try {
      const response = await fetch(`/projects/${projectId}/executive-docs/cable-journal.xlsx`, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Не удалось скачать файл (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Кабельный журнал.xlsx";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const addParticipant = async () => {
    try {
      await api(`/projects/${projectId}/executive-docs/participants`, {
        method: "POST",
        body: JSON.stringify({
          role: pRole,
          full_name: pFullName,
          position: pPosition,
          order_ref: pOrderRef,
          nrs_id: pNrsId,
          object_name: null,
        }),
      });
      setPFullName("");
      setPPosition("");
      setPOrderRef("");
      setPNrsId("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const deleteParticipant = async (id: number) => {
    try {
      await api(`/projects/${projectId}/executive-docs/participants/${id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const buildAosrBody = () => ({
    contract_id: Number(aContractId),
    object_name: aObjectName,
    number: aNumber,
    works: aWorks,
    design_docs: aDesignDocs,
    materials: aMaterials
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [name, quality_doc] = line.split("—").map((s) => s.trim());
        return { name, quality_doc: quality_doc ?? "" };
      }),
    supporting_docs: [] as string[],
    work_start: aWorkStart,
    work_finish: aWorkFinish,
    norms: aNorms.split("\n").map((s) => s.trim()).filter(Boolean),
    next_works: aNextWorks,
  });

  const checkAosr = async () => {
    try {
      const res = await api<{ missing?: string[]; ok?: boolean }>(
        `/projects/${projectId}/executive-docs/aosr/check`,
        { method: "POST", body: JSON.stringify(buildAosrBody()) },
      );
      const missing = res.missing ?? [];
      setAosrMissing(missing);
      setAosrOk(missing.length === 0);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const downloadAosr = async () => {
    try {
      const csrfToken = cookie("pu_csrf");
      const response = await fetch(`/projects/${projectId}/executive-docs/aosr/docx`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        },
        body: JSON.stringify(buildAosrBody()),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `АОСР-${aNumber}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const objects = report?.objects ?? [];
  const objectNames = objects.map((o) => o.object_name);

  return (
    <div className="executive-docs">
      {error && <p role="alert">{error}</p>}

      {objects.length === 0 ? (
        <section className="id-object">
          <h3>Папки ИД ещё не подключены</h3>
          <div className="id-form">
            <label>
              Объект
              <input
                aria-label="Объект"
                value={newObjectName}
                onChange={(e) => setNewObjectName(e.target.value)}
              />
            </label>
            <label>
              ID папки Google Диска
              <input
                aria-label="ID папки"
                value={newFolderId}
                onChange={(e) => setNewFolderId(e.target.value)}
              />
            </label>
            <button type="button" onClick={() => void connectFolder()}>
              Подключить папку
            </button>
          </div>
        </section>
      ) : null}

      <p>
        <button type="button" onClick={() => void downloadCableJournal()}>Скачать заготовку кабельного журнала</button>{" "}
        <span>Кабели берутся из подтверждённых позиций рабочей документации; трассы и фактическую длину впишите в таблице.</span>
      </p>

      <IdOrders projectId={projectId} />
      <EquipmentTestAct projectId={projectId} objectName={objects[0]?.object_name} />
      <InsulationProtocol projectId={projectId} objectName={objects[0]?.object_name} />
      <ProtocolForm projectId={projectId} objectName={objects[0]?.object_name} config={EARTHING_PROTOCOL} />
      <ProtocolForm projectId={projectId} objectName={objects[0]?.object_name} config={PHASE_ZERO_PROTOCOL} />

      {objects.map((o) => {
        const source = sourceFor(o.object_name);
        const isBusy = !!busy[o.object_name];
        return (
          <section key={o.object_name} className="id-object">
            <h3>
              {o.object_name} — готовность {o.percent}%
            </h3>
            <div className="id-progress" aria-hidden="true">
              <div className="id-progress-fill" style={{ width: `${o.percent}%` }} />
            </div>
            <p>
              Обязательных: {o.present} из {o.required_total}, черновиков: {o.draft}
            </p>
            <p>Последняя проверка: {formatDate(o.last_scan_at)}</p>
            {source && (
              <button
                type="button"
                aria-label={`Проверить ИД ${o.object_name}`}
                disabled={isBusy}
                onClick={() => void scan(o.object_name, source.id)}
              >
                {isBusy ? "Проверяем…" : "Проверить сейчас"}
              </button>
            )}

            {o.missing.length > 0 && (
              <div>
                <h4>Нет обязательного</h4>
                {source && (
                  <p>
                    <input
                      id={`id-upload-${source.id}`}
                      type="file"
                      multiple
                      hidden
                      aria-label={`Выбрать файлы для ${o.object_name}`}
                      onChange={(e) => { void uploadFiles(o.object_name, source.id, Array.from(e.target.files ?? [])); e.currentTarget.value = ""; }}
                    />
                    <button type="button" disabled={isBusy} onClick={() => document.getElementById(`id-upload-${source.id}`)?.click()}>
                      Загрузить недостающие документы
                    </button>
                  </p>
                )}
                {uploadNote[o.object_name] && <p role="status">{uploadNote[o.object_name]}</p>}
                <ul className="id-missing">
                  {o.missing.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              </div>
            )}

            <details>
              <summary>Рекомендуется по нормам</summary>
              {o.recommended_missing.length === 0 ? (
                <p>—</p>
              ) : (
                <ul>
                  {o.recommended_missing.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
            </details>

            <details>
              <summary>Замечания к файлам</summary>
              {o.file_issues.length === 0 ? (
                <p>—</p>
              ) : (
                <ul>
                  {o.file_issues.map((fi) => (
                    <li key={fi.path}>
                      {fi.path} — {fi.issues.map((code) => ISSUE_LABELS[code] ?? code).join(", ")}
                    </li>
                  ))}
                </ul>
              )}
            </details>

            <details>
              <summary>Пустые папки</summary>
              {o.empty_folders.length === 0 ? (
                <p>—</p>
              ) : (
                <ul>
                  {o.empty_folders.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              )}
            </details>

            <details>
              <summary>Дубли</summary>
              {o.duplicates.length === 0 ? (
                <p>—</p>
              ) : (
                <ul>
                  {o.duplicates.map(([key, list]) => (
                    <li key={key}>
                      {key}: {list.join(", ")}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </section>
        );
      })}

      <section className="id-object">
        <h3>Участники и представители</h3>
        <table>
          <thead>
            <tr>
              <th>Роль</th>
              <th>ФИО</th>
              <th>Должность</th>
              <th>Приказ</th>
              <th>ИНРС</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {participants.map((p) => (
              <tr key={p.id}>
                <td>{ROLE_LABELS[p.role] ?? p.role}</td>
                <td>{p.full_name}</td>
                <td>{p.position ?? ""}</td>
                <td>{p.order_ref ?? ""}</td>
                <td>{p.nrs_id ?? ""}</td>
                <td>
                  <button type="button" onClick={() => void deleteParticipant(p.id)}>
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="id-form">
          <label>
            Роль
            <select aria-label="Роль" value={pRole} onChange={(e) => setPRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            ФИО
            <input aria-label="ФИО" value={pFullName} onChange={(e) => setPFullName(e.target.value)} />
          </label>
          <label>
            Должность
            <input
              aria-label="Должность"
              value={pPosition}
              onChange={(e) => setPPosition(e.target.value)}
            />
          </label>
          <label>
            Приказ (номер и дата)
            <input
              aria-label="Приказ (номер и дата)"
              value={pOrderRef}
              onChange={(e) => setPOrderRef(e.target.value)}
            />
          </label>
          <label>
            ИНРС
            <input aria-label="ИНРС" value={pNrsId} onChange={(e) => setPNrsId(e.target.value)} />
          </label>
          <button type="button" onClick={() => void addParticipant()}>
            Добавить участника
          </button>
        </div>
      </section>

      <section className="id-object">
        <h3>Сформировать АОСР</h3>
        <div className="id-form">
          <label>
            Договор (ID)
            <input
              type="number"
              aria-label="Договор"
              value={aContractId}
              onChange={(e) => setAContractId(e.target.value)}
            />
          </label>
          <label>
            Объект
            <select
              aria-label="Объект АОСР"
              value={aObjectName}
              onChange={(e) => setAObjectName(e.target.value)}
            >
              <option value="" />
              {objectNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label>
            Номер акта
            <input
              aria-label="Номер акта"
              value={aNumber}
              onChange={(e) => setANumber(e.target.value)}
            />
          </label>
          <label>
            Работы (п. 1)
            <textarea
              aria-label="Работы (п. 1)"
              value={aWorks}
              onChange={(e) => setAWorks(e.target.value)}
            />
          </label>
          <label>
            Чертежи (п. 2)
            <textarea
              aria-label="Чертежи (п. 2)"
              value={aDesignDocs}
              onChange={(e) => setADesignDocs(e.target.value)}
            />
          </label>
          <label>
            Материалы (по строке: наименование — документ о качестве)
            <textarea
              aria-label="Материалы"
              value={aMaterials}
              onChange={(e) => setAMaterials(e.target.value)}
            />
          </label>
          <label>
            Начало работ
            <input
              type="date"
              aria-label="Начало работ"
              value={aWorkStart}
              onChange={(e) => setAWorkStart(e.target.value)}
            />
          </label>
          <label>
            Окончание работ
            <input
              type="date"
              aria-label="Окончание работ"
              value={aWorkFinish}
              onChange={(e) => setAWorkFinish(e.target.value)}
            />
          </label>
          <label>
            Нормы (п. 6, по строке)
            <textarea
              aria-label="Нормы (п. 6)"
              value={aNorms}
              onChange={(e) => setANorms(e.target.value)}
            />
          </label>
          <label>
            Последующие работы (п. 7)
            <textarea
              aria-label="Последующие работы (п. 7)"
              value={aNextWorks}
              onChange={(e) => setANextWorks(e.target.value)}
            />
          </label>
          <button type="button" onClick={() => void checkAosr()}>
            Проверить
          </button>
          <button type="button" onClick={() => void downloadAosr()}>
            Скачать Word
          </button>
        </div>
        {aosrMissing && aosrMissing.length > 0 && (
          <div className="id-missing">
            <p>Не хватает:</p>
            <ul>
              {aosrMissing.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}
        {aosrOk && aosrMissing && aosrMissing.length === 0 && (
          <p>Все обязательные реквизиты заполнены</p>
        )}
      </section>
    </div>
  );
}
