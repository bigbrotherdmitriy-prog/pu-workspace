export const EVIDENCE_FRAGMENT_SCHEMA_VERSION = "evidence-fragment.v54.1" as const;

type Capability = "allow" | "deny" | "unknown";
type EvidenceStatus = "verified" | "unverified" | "stale" | "unavailable";
type VersionState = "current" | "historical" | "changed" | "unknown";
type Freshness = "fresh" | "stale" | "unknown";
type Availability = "available" | "unavailable" | "revoked" | "purged" | "expired" | "unknown";
type Verification = "verified" | "unverified";
type ConfidenceKind = "heuristic" | "model" | "calibrated" | "unknown";

const SAFE_GENERIC_REASON = "Не удалось безопасно проверить доказательство.";

const REASON_LABELS = {
  access_revoked: "Доступ к источнику отозван.",
  provider_unavailable: "Источник временно недоступен.",
  source_not_found: "Источник больше недоступен.",
  source_revision_changed: "Источник изменился; фрагмент относится к другой версии.",
  fragment_expired: "Срок хранения фрагмента истёк.",
  fragment_purged: "Фрагмент удалён по правилам хранения.",
  policy_denied: "Просмотр фрагмента не разрешён.",
  metadata_denied: "Сведения о доказательстве недоступны.",
  verification_pending: "Доказательство ожидает проверки человеком.",
  version_unknown: "Точная версия источника не подтверждена.",
  capability_unknown: "Право на просмотр фрагмента не подтверждено.",
  retention_unknown: "Правила хранения фрагмента не подтверждены.",
  no_content_version: "Нет достаточно точной версии содержимого.",
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

type VisibleViewModel = {
  kind: "evidence";
  metadataVisible: true;
  fragmentVisible: boolean;
  status: EvidenceStatus;
  statusLabel: string;
  reasonLabel: string | null;
  historical: boolean;
  source: {
    provider: string;
    account: string;
    namespace: string;
    originProject: string;
  };
  pins: {
    evidence: string;
    source: string;
    sourceVersion: string;
  };
  locator: EvidenceLocatorViewModel;
  fragment: { mediaType: string; excerpt: string } | null;
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

type HiddenViewModel = {
  kind: "hidden";
  metadataVisible: false;
  fragmentVisible: false;
  status: "unavailable";
  statusLabel: "Недоступно";
  reasonLabel: string;
};

export type EvidenceFragmentViewModel = VisibleViewModel | HiddenViewModel;

type Dictionary = Record<string, unknown>;

function hidden(reasonLabel = SAFE_GENERIC_REASON): HiddenViewModel {
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

function string(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function nullableString(value: unknown): string | null | undefined {
  if (value === null) return null;
  return string(value) ?? undefined;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function enumValue<const T extends readonly string[]>(value: unknown, values: T): T[number] | null {
  return typeof value === "string" && values.includes(value) ? value as T[number] : null;
}

function timestamp(value: unknown): string | null | undefined {
  if (value === null) return null;
  if (typeof value !== "string" || value.trim() === "" || !Number.isFinite(Date.parse(value))) return undefined;
  return value;
}

function numberTuple(value: unknown, length: number): number[] | null {
  if (!Array.isArray(value) || value.length !== length) return null;
  const result = value.map(finiteNumber);
  return result.every((item): item is number => item !== null) ? result : null;
}

function parseLocator(value: unknown): EvidenceLocatorViewModel | null {
  const input = record(value);
  const kind = input && string(input.kind);
  if (!input || !kind) return null;
  if (kind === "whole_object") {
    return input.reason_code === "content_read_forbidden"
      ? { kind, label: "Весь объект · точная область не определена", preciseNavigation: false }
      : null;
  }
  if (kind === "page") {
    const page = positiveInteger(input.page);
    return page ? { kind, label: `Страница ${page}`, preciseNavigation: true } : null;
  }
  if (kind === "page_bbox") {
    const page = positiveInteger(input.page);
    const coordinateSpace = enumValue(input.coordinate_space, ["pixels", "points", "normalized"] as const);
    const box = numberTuple(input.box, 4);
    const extent = numberTuple(input.extent, 2);
    const representationId = string(input.representation_id);
    if (!page || !coordinateSpace || !box || !extent || !representationId) return null;
    const [x, y, width, height] = box;
    const [extentWidth, extentHeight] = extent;
    if (x < 0 || y < 0 || width <= 0 || height <= 0 || extentWidth <= 0 || extentHeight <= 0
      || x + width > extentWidth || y + height > extentHeight || typeof input.precise_navigation !== "boolean") return null;
    return {
      kind,
      label: `Страница ${page} · область ${box.join(", ")} ${coordinateSpace} · representation ${representationId}`,
      preciseNavigation: input.precise_navigation,
    };
  }
  if (kind === "section_clause") {
    if (!Array.isArray(input.section_path) || input.section_path.length === 0) return null;
    const parts = input.section_path.map(string);
    if (!parts.every((part): part is string => part !== null)) return null;
    const clause = nullableString(input.clause_label);
    const anchor = nullableString(input.anchor);
    if (clause === undefined || anchor === undefined) return null;
    return {
      kind,
      label: `${parts.join(" › ")}${clause ? ` · ${clause}` : ""}`,
      preciseNavigation: anchor !== null,
    };
  }
  if (kind === "sheet_cell") {
    const sheetKey = string(input.sheet_key);
    const sheetName = nullableString(input.sheet_name);
    const range = string(input.range_a1);
    const valueKind = enumValue(input.value_kind, ["formula", "cached_value", "displayed_value"] as const);
    if (!sheetKey || sheetName === undefined || !range || !valueKind) return null;
    return { kind, label: `${sheetName ?? sheetKey} · ${range} · ${valueKind}`, preciseNavigation: true };
  }
  if (kind === "message") {
    const messageId = string(input.message_external_id);
    const part = enumValue(input.part, ["body", "subject"] as const);
    const range = input.char_range === null ? null : numberTuple(input.char_range, 2);
    if (!messageId || !part || range === null && input.char_range !== null) return null;
    if (range && (range[0] < 0 || range[1] <= range[0])) return null;
    return {
      kind,
      label: `Сообщение ${messageId} · ${part}${range ? ` · символы ${range[0]}–${range[1]}` : ""}`,
      preciseNavigation: range !== null,
    };
  }
  if (kind === "attachment") {
    const messageId = string(input.message_external_id);
    const attachmentId = string(input.attachment_external_id);
    const sourceId = string(input.attachment_source_reference_id);
    return messageId && attachmentId && sourceId
      ? { kind, label: `Вложение ${attachmentId} · сообщение ${messageId} · source ${sourceId}`, preciseNavigation: true }
      : null;
  }
  if (kind === "record") {
    const recordKey = string(input.record_key);
    if (!Array.isArray(input.field_path) || input.field_path.length === 0) return null;
    const parts = input.field_path.map(string);
    return recordKey && parts.every((part): part is string => part !== null)
      ? { kind, label: `Запись ${recordKey} · ${parts.join(".")}`, preciseNavigation: true }
      : null;
  }
  return null;
}

function statusLabel(status: EvidenceStatus): string {
  return {
    verified: "Проверено",
    unverified: "Не проверено",
    stale: "Устарело",
    unavailable: "Недоступно",
  }[status];
}

function confidenceLabel(value: number | null, kind: ConfidenceKind): string {
  if (value === null || kind === "unknown") return "Оценка извлечения: не оценено";
  const formatted = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 }).format(value);
  const kindLabel = { heuristic: "эвристика", model: "модель", calibrated: "калиброванная", unknown: "" }[kind];
  return `Оценка извлечения: ${formatted} · ${kindLabel}`;
}

/** Converts an untrusted server response to a display-only, fail-closed model. */
export function toEvidenceFragmentViewModel(input: unknown): EvidenceFragmentViewModel {
  const root = record(input);
  if (!root || root.schema_version !== EVIDENCE_FRAGMENT_SCHEMA_VERSION) return hidden();

  const capabilities = record(root.capabilities);
  const metadataCapability = capabilities && enumValue(capabilities.metadata, ["allow", "deny", "unknown"] as const);
  if (metadataCapability !== "allow") return hidden();
  const fragmentCapability = capabilities && enumValue(capabilities.fragment, ["allow", "deny", "unknown"] as const);
  const archivalCapability = capabilities && enumValue(capabilities.archival_fragment, ["allow", "deny", "unknown"] as const);
  if (!fragmentCapability || !archivalCapability) return hidden();

  const policy = record(root.policy);
  if (!policy || typeof policy.known !== "boolean") return hidden();
  const policyVersion = nullableString(policy.version);
  if (policyVersion === undefined || policy.known && policyVersion === null || !policy.known && policyVersion !== null) return hidden();

  const evidence = record(root.evidence);
  const source = record(root.source);
  const sourceVersion = record(root.source_version);
  const assessment = record(root.assessment);
  const extractor = record(root.extractor);
  const confidence = record(root.confidence);
  const fragment = record(root.fragment);
  if (!evidence || !source || !sourceVersion || !assessment || !extractor || !confidence || !fragment) return hidden();

  const evidenceId = string(evidence.id);
  const evidenceRevision = positiveInteger(evidence.revision);
  const evidenceSourceId = string(evidence.source_id);
  const evidenceSourceVersionId = string(evidence.source_version_id);
  const sourceId = string(source.id);
  const sourceRecordVersion = positiveInteger(source.record_version);
  const currentSourceVersionId = string(source.current_source_version_id);
  const sourceVersionId = string(sourceVersion.id);
  const sourceVersionRevision = positiveInteger(sourceVersion.revision);
  const sourceVersionSourceId = string(sourceVersion.source_id);
  if (!evidenceId || !evidenceRevision || !evidenceSourceId || !evidenceSourceVersionId || !sourceId
    || !sourceRecordVersion || !currentSourceVersionId || !sourceVersionId || !sourceVersionRevision
    || !sourceVersionSourceId || evidenceSourceId !== sourceId || sourceVersionSourceId !== sourceId
    || evidenceSourceVersionId !== sourceVersionId) return hidden();

  const status = enumValue(evidence.status, ["verified", "unverified", "stale", "unavailable"] as const);
  const versionState = enumValue(source.version_state, ["current", "historical", "changed", "unknown"] as const);
  const freshness = enumValue(source.freshness, ["fresh", "stale", "unknown"] as const);
  const availability = enumValue(source.availability, ["available", "unavailable", "revoked", "purged", "expired", "unknown"] as const);
  const verification = enumValue(assessment.verification, ["verified", "unverified"] as const);
  const historical = root.historical;
  if (!status || !versionState || !freshness || !availability || !verification || typeof historical !== "boolean") return hidden();
  if (historical !== (versionState === "historical")) return hidden();
  if (versionState === "current" && currentSourceVersionId !== sourceVersionId) return hidden();
  if ((versionState === "historical" || versionState === "changed") && currentSourceVersionId === sourceVersionId) return hidden();
  if (status === "verified" && verification !== "verified" || status === "unverified" && verification !== "unverified") return hidden();
  if (status === "stale" && freshness !== "stale" && versionState !== "changed") return hidden();
  if (status === "unavailable" && availability === "available") return hidden();
  if ((status === "verified" || status === "unverified") && (freshness === "stale" || versionState === "changed" || availability !== "available")) return hidden();

  const rawReason = root.reason_code;
  const reasonCode = rawReason === null ? null : enumValue(rawReason, Object.keys(REASON_LABELS) as ReasonCode[]);
  if (rawReason !== null && reasonCode === null) return hidden();
  if (reasonCode === "access_revoked" && availability !== "revoked"
    || reasonCode === "provider_unavailable" && availability !== "unavailable"
    || reasonCode === "source_not_found" && availability !== "unavailable"
    || reasonCode === "source_revision_changed" && versionState !== "changed" && freshness !== "stale"
    || reasonCode === "fragment_expired" && availability !== "expired"
    || reasonCode === "fragment_purged" && availability !== "purged"
    || reasonCode === "policy_denied" && policy.known && fragmentCapability === "allow"
    || reasonCode === "metadata_denied"
    || reasonCode === "verification_pending" && status !== "unverified"
    || reasonCode === "version_unknown" && versionState !== "unknown" && freshness !== "unknown"
    || reasonCode === "capability_unknown" && fragmentCapability !== "unknown" && archivalCapability !== "unknown"
    || reasonCode === "retention_unknown" && policy.known) return hidden();

  const provider = string(source.provider);
  const account = string(source.account);
  const namespace = string(source.namespace);
  const originProject = string(source.origin_project);
  const locator = parseLocator(root.locator);
  const mediaType = string(fragment.media_type);
  const excerpt = string(fragment.excerpt);
  const extractorName = string(extractor.name);
  const extractorVersion = string(extractor.version);
  const method = string(extractor.method);
  const modelProvider = nullableString(extractor.model_provider);
  const modelId = nullableString(extractor.model_id);
  const modelVersion = nullableString(extractor.model_version);
  const promptVersion = nullableString(extractor.prompt_version);
  if (!provider || !account || !namespace || !originProject || !locator || !mediaType || !excerpt
    || !extractorName || !extractorVersion || !method || modelProvider === undefined || modelId === undefined
    || modelVersion === undefined || promptVersion === undefined) return hidden();
  if ([modelProvider, modelId, modelVersion].filter((value) => value !== null).length !== 0
    && [modelProvider, modelId, modelVersion].some((value) => value === null)) return hidden();

  const confidenceKind = enumValue(confidence.kind, ["heuristic", "model", "calibrated", "unknown"] as const);
  const confidenceValue = confidence.value === null ? null : finiteNumber(confidence.value);
  const calibration = nullableString(confidence.calibration_ref);
  if (!confidenceKind || confidenceValue === null && confidence.value !== null || calibration === undefined
    || confidenceValue !== null && (confidenceValue < 0 || confidenceValue > 1)
    || confidenceKind === "unknown" && (confidenceValue !== null || calibration !== null)
    || confidenceKind === "calibrated" && calibration === null) return hidden();

  const assessmentVersion = positiveInteger(assessment.record_version);
  const reviewer = nullableString(assessment.reviewer);
  const reviewedAt = timestamp(assessment.reviewed_at);
  if (!assessmentVersion || reviewer === undefined || reviewedAt === undefined) return hidden();
  if (verification === "verified" && (!reviewer || !reviewedAt) || verification === "unverified" && (reviewer !== null || reviewedAt !== null)) return hidden();

  const extractedFact = nullableString(root.extracted_fact);
  const aiConclusion = nullableString(root.ai_conclusion);
  if (extractedFact === undefined || aiConclusion === undefined) return hidden();

  const currentReadable = versionState === "current" && freshness === "fresh" && availability === "available";
  const historicalReadable = historical && freshness === "fresh" && availability === "available" && archivalCapability === "allow";
  const fragmentVisible = policy.known && fragmentCapability === "allow" && archivalCapability !== "unknown"
    && (currentReadable || historicalReadable);
  const safeReason = reasonCode ? REASON_LABELS[reasonCode] : fragmentVisible ? null
    : !policy.known ? REASON_LABELS.retention_unknown
      : fragmentCapability === "unknown" ? REASON_LABELS.capability_unknown
        : versionState === "unknown" ? REASON_LABELS.version_unknown
          : status === "stale" ? REASON_LABELS.source_revision_changed
            : REASON_LABELS.policy_denied;

  return {
    kind: "evidence",
    metadataVisible: true,
    fragmentVisible,
    status,
    statusLabel: statusLabel(status),
    reasonLabel: safeReason,
    historical,
    source: { provider, account, namespace, originProject },
    pins: {
      evidence: `${evidenceId}/r${evidenceRevision}`,
      source: `${sourceId}/r${sourceRecordVersion}`,
      sourceVersion: `${sourceVersionId}/r${sourceVersionRevision}`,
    },
    locator,
    fragment: fragmentVisible ? { mediaType, excerpt } : null,
    extractedFact: fragmentVisible ? extractedFact : null,
    aiConclusion: fragmentVisible ? aiConclusion : null,
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
      label: verification === "verified" ? "Проверено человеком" : "Не проверено",
      reviewer,
      reviewedAt,
      version: assessmentVersion,
    },
  };
}
