export const EVIDENCE_FRAGMENT_SCHEMA_VERSION = "evidence-fragment.v54.2" as const;

type Verification = "verified" | "unverified";
type ConfidenceKind = "heuristic" | "model" | "calibrated" | "unknown";
type Dictionary = Record<string, unknown>;

const SAFE_GENERIC_REASON = "Не удалось безопасно проверить доказательство.";
const REASON_LABELS = {
  access_revoked: "Доступ к доказательству отозван.",
  provider_unavailable: "Доказательство временно недоступно.",
  source_not_found: "Доказательство больше недоступно.",
  source_revision_changed: "Версия доказательства изменилась.",
  fragment_expired: "Срок хранения доказательства истёк.",
  fragment_purged: "Доказательство удалено по правилам хранения.",
  policy_denied: "Просмотр доказательства не разрешён.",
  retention_unknown: "Правила хранения доказательства не подтверждены.",
  version_unknown: "Точная версия доказательства не подтверждена.",
  resource_unavailable: "Доказательство недоступно.",
} as const;
type ReasonCode = keyof typeof REASON_LABELS;

export type EvidenceLocatorViewModel =
  | { kind: "whole_object"; label: string; preciseNavigation: false }
  | { kind: "page"; label: string; preciseNavigation: true }
  | { kind: "page_bbox"; label: string; preciseNavigation: boolean }
  | { kind: "section_clause"; label: string; preciseNavigation: boolean }
  | { kind: "sheet_cell"; label: string; preciseNavigation: true }
  | { kind: "message"; label: string; preciseNavigation: boolean }
  | { kind: "attachment"; label: string; preciseNavigation: true }
  | { kind: "record"; label: string; preciseNavigation: true };

type ReadableViewModel = {
  kind: "evidence";
  metadataVisible: true;
  fragmentVisible: true;
  status: Verification;
  statusLabel: string;
  reasonLabel: null;
  historical: boolean;
  source: { provider: string; account: string; namespace: string; originProject: string };
  pins: { evidence: string; source: string; sourceVersion: string };
  locator: EvidenceLocatorViewModel;
  fragment: { mediaType: string; excerpt: string };
  extractedFact: string | null;
  aiConclusion: string | null;
  extraction: {
    extractor: string;
    method: string;
    model: string | null;
    prompt: string | null;
    confidence: string;
    calibration: string | null;
  };
  assessment: {
    verification: Verification;
    label: string;
    reviewer: string | null;
    reviewedAt: string | null;
    version: number;
  };
};

type UnavailableViewModel = {
  kind: "hidden";
  metadataVisible: false;
  fragmentVisible: false;
  status: "unavailable";
  statusLabel: "Недоступно";
  reasonLabel: string;
};

export type EvidenceFragmentViewModel = ReadableViewModel | UnavailableViewModel;

function unavailable(reasonLabel = SAFE_GENERIC_REASON): UnavailableViewModel {
  return {
    kind: "hidden",
    metadataVisible: false,
    fragmentVisible: false,
    status: "unavailable",
    statusLabel: "Недоступно",
    reasonLabel,
  };
}

function record(value: unknown): Dictionary | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Dictionary
    : null;
}

function exact(input: Dictionary, keys: readonly string[]): boolean {
  const actual = Object.keys(input);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.trim() === value ? value : null;
}

function nullableText(value: unknown): string | null | undefined {
  return value === null ? null : text(value) ?? undefined;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function timestamp(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === "string" && value !== "" && Number.isFinite(Date.parse(value)) ? value : undefined;
}

function tuple(value: unknown, length: number): number[] | null {
  if (!Array.isArray(value) || value.length !== length) return null;
  const result = value.map(finiteNumber);
  return result.every((item): item is number => item !== null) ? result : null;
}

function parseLocator(value: unknown): EvidenceLocatorViewModel | null {
  const input = record(value);
  const kind = input && text(input.kind);
  if (!input || !kind) return null;
  if (kind === "whole_object") {
    return exact(input, ["kind", "reason_code"]) && text(input.reason_code)
      ? { kind, label: "Весь объект · точная область не определена", preciseNavigation: false }
      : null;
  }
  if (kind === "page") {
    const page = positiveInteger(input.page);
    return exact(input, ["kind", "page"]) && page
      ? { kind, label: `Страница ${page}`, preciseNavigation: true }
      : null;
  }
  if (kind === "page_bbox") {
    if (!exact(input, ["kind", "page", "coordinate_space", "units", "box", "extent", "representation_id", "precise_navigation"])) return null;
    const page = positiveInteger(input.page);
    const coordinateSpace = input.coordinate_space === "original" || input.coordinate_space === "representation"
      ? input.coordinate_space : null;
    const units = input.units === "pixels" || input.units === "points" || input.units === "normalized"
      ? input.units : null;
    const box = tuple(input.box, 4);
    const extent = tuple(input.extent, 2);
    const representationId = text(input.representation_id);
    if (!page || !coordinateSpace || !units || !box || !extent || !representationId
      || typeof input.precise_navigation !== "boolean") return null;
    const [x, y, width, height] = box;
    const [extentWidth, extentHeight] = extent;
    if (x < 0 || y < 0 || width <= 0 || height <= 0 || extentWidth <= 0 || extentHeight <= 0
      || x + width > extentWidth || y + height > extentHeight
      || units === "normalized" && (extentWidth !== 1 || extentHeight !== 1)) return null;
    return {
      kind,
      label: `Страница ${page} · область ${box.join(", ")} ${units} · ${coordinateSpace}`,
      preciseNavigation: input.precise_navigation,
    };
  }
  if (kind === "section_clause") {
    if (!exact(input, ["kind", "section_path", "clause_label", "anchor"])
      || !Array.isArray(input.section_path) || input.section_path.length === 0) return null;
    const path = input.section_path.map(text);
    const clause = nullableText(input.clause_label);
    const anchor = nullableText(input.anchor);
    if (!path.every((part): part is string => part !== null) || clause === undefined || anchor === undefined) return null;
    return {
      kind,
      label: `${path.join(" › ")}${clause ? ` · ${clause}` : ""}`,
      preciseNavigation: anchor !== null,
    };
  }
  if (kind === "sheet_cell") {
    if (!exact(input, ["kind", "sheet_key", "sheet_name", "range_a1", "value_kind"])) return null;
    const sheetKey = text(input.sheet_key);
    const sheetName = nullableText(input.sheet_name);
    const range = text(input.range_a1);
    const valueKind = input.value_kind === "formula" || input.value_kind === "cached_value"
      || input.value_kind === "displayed_value" ? input.value_kind : null;
    return sheetKey && sheetName !== undefined && range && valueKind
      ? { kind, label: `${sheetName ?? sheetKey} · ${range} · ${valueKind}`, preciseNavigation: true }
      : null;
  }
  if (kind === "message") {
    if (!exact(input, ["kind", "message_external_id", "part", "char_range"])) return null;
    const messageId = text(input.message_external_id);
    const part = input.part === "body" || input.part === "subject" ? input.part : null;
    const range = input.char_range === null ? null : tuple(input.char_range, 2);
    if (!messageId || !part || range === null && input.char_range !== null
      || range && (range[0] < 0 || range[1] <= range[0])) return null;
    return {
      kind,
      label: `Сообщение ${messageId} · ${part}${range ? ` · символы ${range[0]}–${range[1]}` : ""}`,
      preciseNavigation: range !== null,
    };
  }
  if (kind === "attachment") {
    if (!exact(input, ["kind", "message_external_id", "attachment_external_id", "attachment_source_reference_id"])) return null;
    const messageId = text(input.message_external_id);
    const attachmentId = text(input.attachment_external_id);
    const sourceId = text(input.attachment_source_reference_id);
    return messageId && attachmentId && sourceId
      ? { kind, label: `Вложение ${attachmentId} · сообщение ${messageId} · source ${sourceId}`, preciseNavigation: true }
      : null;
  }
  if (kind === "record") {
    if (!exact(input, ["kind", "record_key", "field_path"]) || !Array.isArray(input.field_path)
      || input.field_path.length === 0) return null;
    const recordKey = text(input.record_key);
    const path = input.field_path.map(text);
    return recordKey && path.every((part): part is string => part !== null)
      ? { kind, label: `Запись ${recordKey} · ${path.join(".")}`, preciseNavigation: true }
      : null;
  }
  return null;
}

function confidenceLabel(value: number | null, kind: ConfidenceKind): string {
  if (value === null || kind === "unknown") return "Оценка извлечения: не оценено";
  const formatted = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 }).format(value);
  const label = { heuristic: "эвристика", model: "модель", calibrated: "калиброванная", unknown: "" }[kind];
  return `Оценка извлечения: ${formatted} · ${label}`;
}

/** Parses the server authorization decision; it never derives access locally. */
export function toEvidenceFragmentViewModel(value: unknown): EvidenceFragmentViewModel {
  const root = record(value);
  if (!root || root.schema_version !== EVIDENCE_FRAGMENT_SCHEMA_VERSION) return unavailable();
  if (root.state === "unavailable") {
    if (!exact(root, ["schema_version", "state", "status", "reason_code"]) || root.status !== "unavailable") return unavailable();
    const reason = typeof root.reason_code === "string" && root.reason_code in REASON_LABELS
      ? root.reason_code as ReasonCode : null;
    return reason ? unavailable(REASON_LABELS[reason]) : unavailable();
  }
  if (root.state !== "readable" || !exact(root, [
    "schema_version", "state", "status", "version_state", "freshness", "availability",
    "evidence", "source", "source_version", "locator", "fragment", "extracted_fact",
    "ai_conclusion", "extractor", "confidence", "assessment",
  ])) return unavailable();
  if ((root.status !== "verified" && root.status !== "unverified")
    || (root.version_state !== "current" && root.version_state !== "historical")
    || root.freshness !== "fresh" || root.availability !== "available") return unavailable();

  const evidence = record(root.evidence);
  const source = record(root.source);
  const sourceVersion = record(root.source_version);
  const fragment = record(root.fragment);
  const extractor = record(root.extractor);
  const confidence = record(root.confidence);
  const assessment = record(root.assessment);
  if (!evidence || !source || !sourceVersion || !fragment || !extractor || !confidence || !assessment
    || !exact(evidence, ["id", "revision", "source_id", "source_version_id"])
    || !exact(source, ["id", "record_version", "current_source_version_id", "provider", "account", "namespace", "origin_project"])
    || !exact(sourceVersion, ["id", "revision", "source_id"])
    || !exact(fragment, ["media_type", "excerpt"])
    || !exact(extractor, ["name", "version", "method", "model_provider", "model_id", "model_version", "prompt_version"])
    || !exact(confidence, ["value", "kind", "calibration_ref"])
    || !exact(assessment, ["verification", "reviewer", "reviewed_at", "record_version"])) return unavailable();

  const evidenceId = text(evidence.id);
  const evidenceRevision = positiveInteger(evidence.revision);
  const evidenceSourceId = text(evidence.source_id);
  const evidenceSourceVersionId = text(evidence.source_version_id);
  const sourceId = text(source.id);
  const sourceRecordVersion = positiveInteger(source.record_version);
  const currentVersionId = text(source.current_source_version_id);
  const sourceVersionId = text(sourceVersion.id);
  const sourceVersionRevision = positiveInteger(sourceVersion.revision);
  const sourceVersionSourceId = text(sourceVersion.source_id);
  if (!evidenceId || !evidenceRevision || !evidenceSourceId || !evidenceSourceVersionId || !sourceId
    || !sourceRecordVersion || !currentVersionId || !sourceVersionId || !sourceVersionRevision
    || !sourceVersionSourceId || evidenceSourceId !== sourceId || sourceVersionSourceId !== sourceId
    || evidenceSourceVersionId !== sourceVersionId
    || root.version_state === "current" && currentVersionId !== sourceVersionId
    || root.version_state === "historical" && currentVersionId === sourceVersionId) return unavailable();

  const provider = text(source.provider);
  const account = text(source.account);
  const namespace = text(source.namespace);
  const originProject = text(source.origin_project);
  const locator = parseLocator(root.locator);
  const mediaType = text(fragment.media_type);
  const excerpt = text(fragment.excerpt);
  if (!provider || !account || !namespace || !originProject || !locator || !mediaType || !excerpt) return unavailable();

  const extractorName = text(extractor.name);
  const extractorVersion = text(extractor.version);
  const method = text(extractor.method);
  const modelProvider = nullableText(extractor.model_provider);
  const modelId = nullableText(extractor.model_id);
  const modelVersion = nullableText(extractor.model_version);
  const promptVersion = nullableText(extractor.prompt_version);
  const allModelNull = [modelProvider, modelId, modelVersion].every((item) => item === null);
  const allModelPresent = [modelProvider, modelId, modelVersion].every((item) => typeof item === "string");
  if (!extractorName || !extractorVersion || !method || modelProvider === undefined || modelId === undefined
    || modelVersion === undefined || promptVersion === undefined || !allModelNull && !allModelPresent) return unavailable();

  const confidenceKind = confidence.kind === "heuristic" || confidence.kind === "model"
    || confidence.kind === "calibrated" || confidence.kind === "unknown" ? confidence.kind : null;
  const confidenceValue = confidence.value === null ? null : finiteNumber(confidence.value);
  const calibration = nullableText(confidence.calibration_ref);
  if (!confidenceKind || confidenceValue === null && confidence.value !== null || calibration === undefined
    || confidenceValue !== null && (confidenceValue < 0 || confidenceValue > 1)
    || confidenceKind === "unknown" && (confidenceValue !== null || calibration !== null)
    || confidenceKind === "calibrated" && calibration === null) return unavailable();

  const verification = assessment.verification === "verified" || assessment.verification === "unverified"
    ? assessment.verification : null;
  const reviewer = nullableText(assessment.reviewer);
  const reviewedAt = timestamp(assessment.reviewed_at);
  const assessmentVersion = positiveInteger(assessment.record_version);
  if (!verification || verification !== root.status || reviewer === undefined || reviewedAt === undefined
    || !assessmentVersion || verification === "verified" && (!reviewer || !reviewedAt)
    || verification === "unverified" && (reviewer !== null || reviewedAt !== null)) return unavailable();

  const extractedFact = nullableText(root.extracted_fact);
  const aiConclusion = nullableText(root.ai_conclusion);
  if (extractedFact === undefined || aiConclusion === undefined) return unavailable();
  return {
    kind: "evidence",
    metadataVisible: true,
    fragmentVisible: true,
    status: root.status,
    statusLabel: root.status === "verified" ? "Проверено" : "Не проверено",
    reasonLabel: null,
    historical: root.version_state === "historical",
    source: { provider, account, namespace, originProject },
    pins: {
      evidence: `${evidenceId}/r${evidenceRevision}`,
      source: `${sourceId}/r${sourceRecordVersion}`,
      sourceVersion: `${sourceVersionId}/r${sourceVersionRevision}`,
    },
    locator,
    fragment: { mediaType, excerpt },
    extractedFact,
    aiConclusion,
    extraction: {
      extractor: `${extractorName}/${extractorVersion}`,
      method,
      model: modelProvider && modelId && modelVersion ? `${modelProvider} / ${modelId} / ${modelVersion}` : null,
      prompt: promptVersion,
      confidence: confidenceLabel(confidenceValue, confidenceKind),
      calibration,
    },
    assessment: {
      verification,
      label: verification === "verified" ? "Проверено" : "Не проверено",
      reviewer,
      reviewedAt,
      version: assessmentVersion,
    },
  };
}
