import type { EvidencePin } from "../management/managementReadModel";

export const isUuid = (v: unknown): v is string => typeof v === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
export const positiveInt = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
function object(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("invalid_meeting_source");
  return v as Record<string, unknown>;
}
export type MeetingBinding = {
  meetingId: number; meetingRecordVersion: number; bindingId: string;
  sourceId: string; sourceVersionId: string;
};
export type MeetingSource = { sourceId: string; sourceVersionId: string; evidencePins: EvidencePin[] };
export const sameBinding = (a: MeetingBinding | undefined | null, b: MeetingBinding | undefined | null) => !!a && !!b
  && a.meetingId === b.meetingId && a.meetingRecordVersion === b.meetingRecordVersion && a.bindingId === b.bindingId
  && a.sourceId === b.sourceId && a.sourceVersionId === b.sourceVersionId;
export function parseMeetingBinding(raw: unknown): MeetingBinding {
  const r = object(raw);
  if (r.origin_status !== "bound" || r.confirmation_available !== true
    || !positiveInt(r.meeting_id) || !positiveInt(r.meeting_record_version)
    || !isUuid(r.binding_id) || !isUuid(r.source_id) || !isUuid(r.source_version_id)) {
    throw new Error("invalid_meeting_binding");
  }
  return { meetingId: r.meeting_id, meetingRecordVersion: r.meeting_record_version,
    bindingId: r.binding_id, sourceId: r.source_id, sourceVersionId: r.source_version_id };
}
export function parseEligibleSources(raw: unknown, meetingId: number, recordVersion: number) {
  const r = object(raw);
  if (r.external_actions_created !== false || r.meeting_id !== meetingId
    || r.meeting_record_version !== recordVersion || !Array.isArray(r.sources)) throw new Error("stale_meeting_sources");
  if (r.sources.length > 500) throw new Error("source_capacity_exceeded");
  const sources = r.sources.map((entry): MeetingSource => {
    const s = object(entry);
    if (!isUuid(s.source_id) || !isUuid(s.source_version_id) || !Array.isArray(s.evidence_pins)
      || s.evidence_pins.length > 1000) throw new Error("invalid_meeting_sources");
    const evidencePins = s.evidence_pins.map((entry): EvidencePin => {
      const pin = object(entry), ref = object(pin.ref), id = object(ref.id), tenant = object(ref.tenant_id);
      if (pin.version_kind !== "revision" || !positiveInt(pin.value) || ref.namespace !== "pu"
        || ref.type !== "evidence" || id.kind !== "uuid" || !isUuid(id.value)
        || tenant.kind !== "int" || typeof tenant.value !== "string" || !/^[1-9][0-9]{0,18}$/.test(tenant.value)
        || Object.keys(pin).sort().join() !== "ref,value,version_kind"
        || Object.keys(ref).sort().join() !== "id,namespace,tenant_id,type"
        || Object.keys(id).sort().join() !== "kind,value" || Object.keys(tenant).sort().join() !== "kind,value") {
        throw new Error("invalid_evidence_pin");
      }
      return pin;
    });
    if (new Set(evidencePins.map(pin => JSON.stringify(pin))).size !== evidencePins.length) throw new Error("duplicate_evidence_pin");
    return { sourceId: s.source_id, sourceVersionId: s.source_version_id, evidencePins };
  });
  if (new Set(sources.map(s => `${s.sourceId}:${s.sourceVersionId}`)).size !== sources.length) throw new Error("duplicate_source");
  return sources;
}
