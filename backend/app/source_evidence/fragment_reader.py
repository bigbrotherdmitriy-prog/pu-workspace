"""Fail-closed, read-only access to version-pinned evidence fragments.

This module deliberately has no provider, HTTP, filesystem, audit, or queue
integration.  The caller supplies both the authoritative resolver and the
opaque fragment store, and retains ownership of the SQLAlchemy transaction.
"""
from __future__ import annotations

import re
from datetime import datetime
from functools import wraps
from typing import Annotated, Callable, Literal, Protocol, Union

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictBytes,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.core.v54_interfaces import RequestScope, Resolution, require_resolution
from app.core.v54_permissions import SourceEvidenceError, deny, utc
from app.core.v54_refs import ObjectRef, StrictDTO, VersionPin, require_same_tenant
from app.models.v54_pilot import (
    ConnectionIdentity,
    Evidence,
    EvidenceAssessment,
    MailConnection,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)


_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_A1 = re.compile(
    r"(?:(?:'[^'\r\n]+'|[A-Za-z0-9_]{1,100})!)?"
    r"[A-Z]{1,3}[1-9][0-9]*(?::[A-Z]{1,3}[1-9][0-9]*)?\Z"
)
_SAFE_MEDIA_TYPES = frozenset({
    "text/plain",
    "text/plain; charset=utf-8",
    "text/markdown",
    "text/markdown; charset=utf-8",
    "application/json",
    "application/json; charset=utf-8",
})


def _nonempty(value: str, *, limit: int = 1000) -> str:
    if not value or len(value) > limit or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("resource_unavailable")
    return value


class FragmentLimits(StrictDTO):
    max_bytes: StrictInt = 64 * 1024

    @model_validator(mode="after")
    def validate_limit(self):
        if self.max_bytes <= 0 or self.max_bytes > 1024 * 1024:
            raise ValueError("resource_unavailable")
        return self


class RepresentationDescriptor(StrictDTO):
    schema_version: Literal["v54.fragment.1"]
    representation_id: StrictStr
    handle: StrictStr
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    kind: Literal["extracted_text", "quote"]
    media_type: StrictStr
    retention_state: Literal["active", "expired", "purged"]
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_descriptor(self):
        if (not _OPAQUE.fullmatch(self.representation_id)
                or not _OPAQUE.fullmatch(self.handle)
                or self.media_type not in _SAFE_MEDIA_TYPES
                or self.evidence_pin.ref.type != "evidence"
                or self.evidence_pin.version_kind != "revision"
                or self.source_ref.type != "source"
                or self.source_version_pin.ref.type != "source_version"
                or self.source_version_pin.version_kind != "revision"):
            raise ValueError("resource_unavailable")
        require_same_tenant(
            self.evidence_pin.ref.tenant_id,
            self.source_ref,
            self.source_version_pin.ref,
        )
        return self


class PageBBoxLocator(StrictDTO):
    kind: Literal["page_bbox"]
    page: StrictInt
    coordinate_space: Literal["original", "representation"]
    units: Literal["pixels", "points", "normalized"]
    box: tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat]
    extent: tuple[StrictFloat, StrictFloat]
    representation_id: StrictStr
    precise_navigation: StrictBool

    @model_validator(mode="after")
    def validate_geometry(self):
        x, y, width, height = self.box
        extent_width, extent_height = self.extent
        if (self.page < 1 or not _OPAQUE.fullmatch(self.representation_id)
                or x < 0 or y < 0 or width <= 0 or height <= 0
                or extent_width <= 0 or extent_height <= 0
                or x + width > extent_width or y + height > extent_height):
            raise ValueError("resource_unavailable")
        if self.units == "normalized" and (extent_width != 1 or extent_height != 1):
            raise ValueError("resource_unavailable")
        return self


class PageLocator(StrictDTO):
    kind: Literal["page"]
    page: StrictInt

    @model_validator(mode="after")
    def validate_page(self):
        if self.page < 1:
            raise ValueError("resource_unavailable")
        return self


class SectionClauseLocator(StrictDTO):
    kind: Literal["section_clause"]
    section_path: tuple[StrictStr, ...]
    clause_label: StrictStr
    anchor: StrictStr | None

    @model_validator(mode="after")
    def validate_section(self):
        if not self.section_path:
            raise ValueError("resource_unavailable")
        for value in (*self.section_path, self.clause_label):
            _nonempty(value)
        if self.anchor is not None:
            _nonempty(self.anchor)
        return self


class SheetCellLocator(StrictDTO):
    kind: Literal["sheet_cell"]
    sheet_key: StrictStr
    sheet_name: StrictStr
    range_a1: StrictStr
    value_kind: Literal["formula", "cached_value", "displayed_value"]

    @model_validator(mode="after")
    def validate_cell(self):
        _nonempty(self.sheet_key, limit=255)
        _nonempty(self.sheet_name, limit=255)
        if not _A1.fullmatch(self.range_a1):
            raise ValueError("resource_unavailable")
        return self


class MessageLocator(StrictDTO):
    kind: Literal["message"]
    message_external_id: StrictStr
    part: Literal["body", "subject"]
    char_range: tuple[StrictInt, StrictInt] | None

    @model_validator(mode="after")
    def validate_range(self):
        _nonempty(self.message_external_id)
        if self.char_range is not None:
            start, end = self.char_range
            if start < 0 or end <= start:
                raise ValueError("resource_unavailable")
        return self


class TextRangeLocator(StrictDTO):
    kind: Literal["text_range"]
    unit: Literal["unicode_codepoint"]
    start: StrictInt
    end: StrictInt

    @model_validator(mode="after")
    def validate_range(self):
        if self.start < 0 or self.end <= self.start:
            raise ValueError("resource_unavailable")
        return self


class AttachmentLocator(StrictDTO):
    kind: Literal["attachment"]
    message_external_id: StrictStr
    attachment_external_id: StrictStr
    attachment_source_reference_id: ObjectRef

    @model_validator(mode="after")
    def validate_attachment(self):
        _nonempty(self.message_external_id)
        _nonempty(self.attachment_external_id)
        if self.attachment_source_reference_id.type != "source":
            raise ValueError("resource_unavailable")
        return self


class RecordLocator(StrictDTO):
    kind: Literal["record"]
    record_key: StrictStr
    field_path: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def validate_record(self):
        _nonempty(self.record_key)
        if not self.field_path:
            raise ValueError("resource_unavailable")
        for value in self.field_path:
            _nonempty(value)
        return self


class WholeObjectLocator(StrictDTO):
    kind: Literal["whole_object"]
    reason_code: StrictStr

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _nonempty(value, limit=100)


EvidenceLocator = Annotated[
    Union[
        PageBBoxLocator,
        PageLocator,
        SectionClauseLocator,
        SheetCellLocator,
        MessageLocator,
        TextRangeLocator,
        AttachmentLocator,
        RecordLocator,
        WholeObjectLocator,
    ],
    Field(discriminator="kind"),
]
_LOCATOR = TypeAdapter(EvidenceLocator)


class FragmentStoreRequest(StrictDTO):
    representation_id: StrictStr
    handle: StrictStr
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    kind: Literal["extracted_text", "quote"]
    media_type: StrictStr
    max_bytes: StrictInt


class FragmentStorePayload(StrictDTO):
    representation_id: StrictStr
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    kind: Literal["extracted_text", "quote"]
    media_type: StrictStr
    fragment: StrictBytes


class ExtractorMetadata(StrictDTO):
    name: StrictStr
    version: StrictStr | None = None
    method: StrictStr | None = None
    model_provider: StrictStr | None = None
    model_id: StrictStr | None = None
    model_version: StrictStr | None = None
    prompt_version: StrictStr | None = None
    configuration_digest: StrictStr | None = None

    @model_validator(mode="after")
    def validate_metadata(self):
        for value in (
            self.name, self.version, self.method, self.model_provider,
            self.model_id, self.model_version, self.prompt_version,
            self.configuration_digest,
        ):
            if value is not None:
                _nonempty(value, limit=255)
        model_fields = (self.model_provider, self.model_id, self.model_version)
        if any(value is not None for value in model_fields) and any(value is None for value in model_fields):
            raise ValueError("resource_unavailable")
        return self


class FragmentReadResult(StrictDTO):
    evidence_pin: VersionPin
    source_ref: ObjectRef
    source_version_pin: VersionPin
    representation_id: StrictStr
    kind: Literal["extracted_text", "quote"]
    media_type: StrictStr
    verification: Literal["verified", "unverified"]
    effective_status: Literal["verified", "unverified"]
    historical: StrictBool
    assessment_record_version: StrictInt
    version_state: Literal["current", "historical"]
    freshness: Literal["fresh"]
    availability: Literal["available"]
    valid_until: AwareDatetime
    extractor: ExtractorMetadata
    confidence: StrictFloat | None
    confidence_kind: Literal["heuristic", "model", "calibrated", "unknown"]
    extracted_at: AwareDatetime
    locator: EvidenceLocator
    fragment: StrictStr


class FragmentStore(Protocol):
    def read(self, request: FragmentStoreRequest) -> FragmentStorePayload: ...


class AuthoritativeFragmentResolver(Protocol):
    def resolve(
        self,
        db: Session,
        *,
        scope: RequestScope,
        pin: VersionPin,
        operation: str,
        lock: bool,
    ) -> Resolution: ...


def _safe_boundary(function):
    """Collapse every dependency/parser failure to the fragment non-disclosure error."""
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SourceEvidenceError:
            raise
        except Exception:
            raise SourceEvidenceError("resource_unavailable") from None
    return call


def _validate_locator(*, scope: RequestScope, source: SourceReference,
                      parent: SourceReference | None,
                      descriptor: RepresentationDescriptor, raw: object) -> EvidenceLocator:
    locator = _LOCATOR.validate_python(raw)
    if isinstance(locator, PageBBoxLocator):
        if locator.representation_id != descriptor.representation_id or source.object_kind not in {"file", "attachment"}:
            deny()
    elif isinstance(locator, (PageLocator, SectionClauseLocator, SheetCellLocator)):
        if source.object_kind not in {"file", "attachment"}:
            deny()
    elif isinstance(locator, MessageLocator):
        if source.object_kind != "message" or locator.message_external_id != source.external_id:
            deny()
    elif isinstance(locator, TextRangeLocator):
        if source.object_kind not in {"message", "attachment"}:
            deny()
    elif isinstance(locator, AttachmentLocator):
        require_same_tenant(scope.tenant, locator.attachment_source_reference_id)
        if (source.object_kind != "attachment"
                or locator.attachment_source_reference_id.id.value != source.id
                or locator.attachment_external_id != source.external_id
                or not source.parent_source_id):
            deny()
        if (not parent or parent.object_kind != "message"
                or parent.identity_id != source.identity_id
                or parent.namespace != source.namespace
                or parent.origin_project_id != source.origin_project_id
                or locator.message_external_id != parent.external_id):
            deny()
    elif isinstance(locator, RecordLocator):
        if source.object_kind != "record":
            deny()
    return locator


@_safe_boundary
def read_fragment(
    db: Session,
    *,
    scope: RequestScope,
    evidence_pin: VersionPin,
    resolver: AuthoritativeFragmentResolver,
    store: FragmentStore,
    clock: Callable[[], datetime],
    limits: FragmentLimits,
) -> FragmentReadResult:
    """Read one exact current representation without changing caller state."""
    now = utc(clock())
    if (not db.in_transaction() or not isinstance(scope, RequestScope)
            or not isinstance(evidence_pin, VersionPin)
            or not isinstance(limits, FragmentLimits)
            or now is None or now.tzinfo is None
            or evidence_pin.ref.type != "evidence"
            or evidence_pin.version_kind != "revision" or evidence_pin.value != 1):
        deny()
    require_same_tenant(scope.tenant, evidence_pin.ref)
    tenant_id = int(scope.tenant.value)
    project_id = int(scope.project.id.value)

    with db.no_autoflush:
        parent_source = aliased(SourceReference, name="parent_source")
        lineage = db.execute(
            select(
                Evidence,
                EvidenceAssessment,
                SourceReference,
                SourceVersion,
                SourceCurrent,
                ConnectionIdentity,
                MailConnection,
                parent_source,
            )
            .select_from(Evidence)
            .join(EvidenceAssessment, and_(
                EvidenceAssessment.evidence_id == Evidence.id,
                EvidenceAssessment.organization_id == Evidence.organization_id,
            ))
            .join(SourceReference, and_(
                SourceReference.id == Evidence.source_id,
                SourceReference.organization_id == Evidence.organization_id,
            ))
            .join(SourceVersion, and_(
                SourceVersion.id == Evidence.source_version_id,
                SourceVersion.source_id == Evidence.source_id,
                SourceVersion.organization_id == Evidence.organization_id,
            ))
            .join(SourceCurrent, and_(
                SourceCurrent.source_id == Evidence.source_id,
                SourceCurrent.organization_id == Evidence.organization_id,
            ))
            .join(ConnectionIdentity, and_(
                ConnectionIdentity.id == SourceReference.identity_id,
                ConnectionIdentity.organization_id == Evidence.organization_id,
            ))
            .join(MailConnection, and_(
                MailConnection.identity_id == SourceReference.identity_id,
                MailConnection.namespace == SourceReference.namespace,
                MailConnection.organization_id == Evidence.organization_id,
            ))
            .outerjoin(parent_source, and_(
                parent_source.id == SourceReference.parent_source_id,
                parent_source.organization_id == Evidence.organization_id,
            ))
            .where(
                Evidence.id == evidence_pin.ref.id.value,
                Evidence.organization_id == tenant_id,
                Evidence.revision == evidence_pin.value,
                SourceReference.origin_project_id == project_id,
            )
        ).one_or_none()
        if lineage is None:
            deny()
        evidence, assessment, source, version, current, identity, mailbox, parent = lineage
        if (evidence.id != evidence_pin.ref.id.value
                or evidence.organization_id != tenant_id
                or evidence.revision != evidence_pin.value
                or assessment.evidence_id != evidence.id
                or assessment.organization_id != tenant_id
                or source.id != evidence.source_id
                or source.organization_id != tenant_id
                or source.origin_project_id != project_id
                or version.id != evidence.source_version_id
                or version.source_id != source.id
                or version.organization_id != tenant_id
                or current.source_id != source.id
                or current.organization_id != tenant_id
                or identity.id != source.identity_id
                or identity.organization_id != tenant_id
                or mailbox.identity_id != identity.id
                or mailbox.organization_id != tenant_id
                or mailbox.namespace != source.namespace
                or source.parent_source_id is None and parent is not None
                or source.parent_source_id is not None
                and (parent is None or parent.id != source.parent_source_id
                     or parent.organization_id != tenant_id)):
            deny()

        checked_at = utc(assessment.checked_at)
        valid_until = utc(assessment.valid_until)
        source_checked_at = utc(source.last_checked_at)
        source_next_check = utc(source.next_check_at)
        observed_at = utc(version.observed_at)
        verified_at = utc(identity.verified_at) if identity else None
        reviewed_at = utc(assessment.reviewed_at)
        extracted_at = utc(evidence.extracted_at)
        policy_pins = source.policy_pins
        if (not identity or not mailbox or identity.state != "verified" or mailbox.state != "active"
                or identity.binding_epoch <= 0 or identity.record_version <= 0
                or identity.credential_generation is None or identity.credential_generation <= 0
                or verified_at is None or verified_at > now or mailbox.record_version <= 0
                or version.revision != 1 or version.consistency not in {"revision_bound", "digest_observed"}
                or (version.consistency == "revision_bound" and not version.provider_revision)
                or (version.consistency == "digest_observed" and not version.integrity)
                or observed_at is None or observed_at > now
                or source.freshness != "fresh" or source.availability != "available"
                or source_checked_at is None or source_checked_at > now
                or source_next_check is None or source_next_check <= now
                or assessment.freshness != "fresh" or assessment.availability != "available"
                or checked_at is None or checked_at > now or valid_until is None or valid_until <= now
                or assessment.verification not in {"verified", "unverified"}
                or assessment.record_version <= 0
                or (assessment.verification == "verified"
                    and (assessment.reviewed_by is None or reviewed_at is None or reviewed_at > now))
                or extracted_at is None or extracted_at > now
                or not isinstance(policy_pins, dict) or evidence.policy_pins != policy_pins
                or set(policy_pins) != {"access", "retention", "residency"}
                or not isinstance(source.residency, dict) or not source.residency):
            deny()

        version_state = "current" if current.version_id == version.id else "historical"
        resolution = resolver.resolve(db, scope=scope, pin=evidence_pin,
                                      operation="fragment", lock=False)
        if not isinstance(resolution, Resolution):
            deny()
        require_resolution(
            resolution, scope=scope, pin=evidence_pin, operation="fragment", now=now
        )
        if (resolution.binding_epoch != identity.binding_epoch
                or resolution.version != version_state):
            deny()

        extractor = ExtractorMetadata.model_validate(evidence.extractor)
        if (evidence.confidence_kind not in {"heuristic", "model", "calibrated", "unknown"}
                or evidence.confidence is not None
                and (not isinstance(evidence.confidence, float)
                     or evidence.confidence < 0 or evidence.confidence > 1)
                or evidence.confidence_kind == "unknown" and evidence.confidence is not None
                or evidence.confidence_kind != "unknown" and evidence.confidence is None):
            deny()

        descriptor = RepresentationDescriptor.model_validate(evidence.representation_ref)
        source_ref = ObjectRef(
            namespace="pu", type="source", tenant_id=scope.tenant,
            id={"kind": "uuid", "value": source.id},
        )
        source_version_pin = VersionPin(
            ref=ObjectRef(namespace="pu", type="source_version", tenant_id=scope.tenant,
                          id={"kind": "uuid", "value": version.id}),
            version_kind="revision", value=version.revision,
        )
        descriptor_expires_at = utc(descriptor.expires_at)
        resolution_valid_until = utc(resolution.valid_until)
        if (descriptor.retention_state != "active" or descriptor_expires_at is None
                or descriptor_expires_at <= now or resolution_valid_until is None
                or descriptor.evidence_pin != evidence_pin
                or descriptor.source_ref != source_ref
                or descriptor.source_version_pin != source_version_pin):
            deny()
        locator = _validate_locator(
            scope=scope, source=source, parent=parent,
            descriptor=descriptor, raw=evidence.locator
        )
        effective_valid_until = min(
            resolution_valid_until, valid_until, source_next_check, descriptor_expires_at,
        )

        request = FragmentStoreRequest(
            representation_id=descriptor.representation_id,
            handle=descriptor.handle,
            evidence_pin=evidence_pin,
            source_ref=source_ref,
            source_version_pin=source_version_pin,
            kind=descriptor.kind,
            media_type=descriptor.media_type,
            max_bytes=limits.max_bytes,
        )
        try:
            payload = store.read(request)
        except Exception:
            raise SourceEvidenceError("resource_unavailable") from None
        if not isinstance(payload, FragmentStorePayload):
            payload = FragmentStorePayload.model_validate(payload)
        if (payload.representation_id != request.representation_id
                or payload.evidence_pin != request.evidence_pin
                or payload.source_ref != request.source_ref
                or payload.source_version_pin != request.source_version_pin
                or payload.kind != request.kind or payload.media_type != request.media_type
                or not payload.fragment or len(payload.fragment) > limits.max_bytes):
            deny()
        try:
            fragment = payload.fragment.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            deny()
        if not fragment or "\x00" in fragment:
            deny()
        return FragmentReadResult(
            evidence_pin=evidence_pin,
            source_ref=source_ref,
            source_version_pin=source_version_pin,
            representation_id=descriptor.representation_id,
            kind=descriptor.kind,
            media_type=descriptor.media_type,
            verification=assessment.verification,
            effective_status=(
                "verified"
                if assessment.verification == "verified" and resolution.verification == "verified"
                else "unverified"
            ),
            historical=version_state == "historical",
            assessment_record_version=assessment.record_version,
            version_state=version_state,
            freshness="fresh",
            availability="available",
            valid_until=effective_valid_until,
            extractor=extractor,
            confidence=evidence.confidence,
            confidence_kind=evidence.confidence_kind,
            extracted_at=extracted_at,
            locator=locator,
            fragment=fragment,
        )
