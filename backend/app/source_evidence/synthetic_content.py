"""Local, deterministic content bridge for the synthetic v5.4 acceptance corpus.

This is deliberately not a production parser or an AI provider.  It accepts
caller-owned bytes, stores no source text, and can only create unverified
evidence, context hypotheses, and an unverified deadline claim through the
existing A/B/C facades.  Human review remains a separate operation.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import StrictBool, StrictBytes, StrictFloat, StrictInt, StrictStr, model_validator
from sqlalchemy import select

from app.core.v54_dto import DeadlineClaimInput, canonical_json
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, StrictDTO, TaggedId, VersionPin, require_same_tenant
from app.models.organization_contract import Contract
from app.models.project import Project


_PROJECT = re.compile(
    r"(?:\bпо\s+проекту\s+[«\"](?P<quoted>[^»\"\r\n]+)[»\"]"
    r"|^\s*проект\s*:\s*(?P<line>[^.\r\n]+))",
    re.IGNORECASE | re.MULTILINE,
)
_CONTRACT = re.compile(
    r"\bдоговор(?:у)?(?:\s+|\s*:\s*)(?P<value>[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./-]{0,99})",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"\b(?P<day>[0-3]?\d)\s+"
    r"(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
    r"(?P<year>20\d{2})\s+года\b",
    re.IGNORECASE,
)
_TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MAX_SOURCE_BYTES = 64 * 1024


class ContentExtractionError(ValueError):
    """Fixed, content-free failure code only."""


class TextSpan(StrictDTO):
    source_kind: Literal["message", "attachment"]
    start: StrictInt
    end: StrictInt
    confidence: StrictFloat

    @model_validator(mode="after")
    def validate_span(self):
        if self.start < 0 or self.end <= self.start or not 0 <= self.confidence <= 1:
            raise ValueError("content_unavailable")
        return self


class ContentExtraction(StrictDTO):
    status: Literal["ready", "manual_review_required"]
    reason_code: Literal[
        "ready", "content_unavailable", "ambiguous_context",
        "ambiguous_deadline", "deadline_precision_unsupported",
    ]
    project_name: StrictStr | None = None
    contract_number: StrictStr | None = None
    due_date: StrictStr | None = None
    spans: tuple[TextSpan, ...] = ()
    confidence: StrictFloat | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        ready = self.status == "ready"
        if ready != (self.reason_code == "ready"):
            raise ValueError("content_unavailable")
        if ready and (not self.project_name or not self.contract_number or not self.due_date
                      or len(self.spans) != 2 or self.confidence is None):
            raise ValueError("content_unavailable")
        if not ready and (self.spans or self.confidence is not None):
            raise ValueError("content_unavailable")
        return self


class ContentPipelineResult(StrictDTO):
    status: Literal["awaiting_human_review", "manual_review_required"]
    reason_code: StrictStr
    manual_review_required: Literal[True]
    project: VersionPin | None = None
    contract: VersionPin | None = None
    evidence: tuple[VersionPin, ...] = ()
    relations: tuple[VersionPin, ...] = ()
    claim: VersionPin | None = None
    due_date: StrictStr | None = None
    active_project_selected: StrictBool = False


def _decode(value: bytes) -> str:
    if type(value) is not bytes or not value or len(value) > _MAX_SOURCE_BYTES:
        raise ContentExtractionError("content_unavailable")
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ContentExtractionError("content_unavailable") from None
    if text.startswith("\ufeff") or "\x00" in text:
        raise ContentExtractionError("content_unavailable")
    return text


def _unique(matches) -> list[str]:
    values: dict[str, str] = {}
    for value in matches:
        clean = value.strip()
        values.setdefault(clean.casefold(), clean)
    return list(values.values())


def _deadline_values(text: str) -> tuple[list[str], list[re.Match]]:
    matches = list(_DATE.finditer(text))
    values = []
    for match in matches:
        try:
            parsed = date(
                int(match.group("year")),
                _MONTHS[match.group("month").casefold()],
                int(match.group("day")),
            )
        except ValueError:
            return [], matches
        values.append(parsed.isoformat())
    return _unique(values), matches


def _line_span(text: str, match: re.Match) -> tuple[int, int]:
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end < 0:
        end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        raise ContentExtractionError("content_unavailable")
    return start, end


def extract_synthetic_content(*, message: bytes, attachment: bytes) -> ContentExtraction:
    """Extract exact local facts without accepting oracle/candidate hints."""
    try:
        documents = ((_decode(message), "message"), (_decode(attachment), "attachment"))
    except ContentExtractionError:
        return ContentExtraction(status="manual_review_required", reason_code="content_unavailable")

    projects: list[str] = []
    contracts: list[str] = []
    dates_by_document: list[list[str]] = []
    date_matches: list[list[re.Match]] = []
    for text, _kind in documents:
        projects.extend(
            value for match in _PROJECT.finditer(text)
            if (value := (match.group("quoted") or match.group("line")))
        )
        contracts.extend(match.group("value").rstrip(".,;:") for match in _CONTRACT.finditer(text))
        values, matches = _deadline_values(text)
        if any(_TIME.search(text[_line_span(text, match)[0]:_line_span(text, match)[1]])
               for match in matches):
            return ContentExtraction(
                status="manual_review_required",
                reason_code="deadline_precision_unsupported",
            )
        dates_by_document.append(values)
        date_matches.append(matches)

    project_values = _unique(projects)
    contract_values = _unique(contracts)
    if len(project_values) != 1 or len(contract_values) != 1:
        return ContentExtraction(status="manual_review_required", reason_code="ambiguous_context")
    if (any(len(values) != 1 for values in dates_by_document)
            or dates_by_document[0] != dates_by_document[1]
            or any(len(matches) != 1 for matches in date_matches)):
        return ContentExtraction(status="manual_review_required", reason_code="ambiguous_deadline")

    spans = []
    for (text, kind), matches in zip(documents, date_matches, strict=True):
        start, end = _line_span(text, matches[0])
        spans.append(TextSpan(source_kind=kind, start=start, end=end, confidence=1.0))
    return ContentExtraction(
        status="ready",
        reason_code="ready",
        project_name=project_values[0],
        contract_number=contract_values[0],
        due_date=dates_by_document[0][0],
        spans=tuple(spans),
        confidence=1.0,
    )


def _record_pin(scope: RequestScope, kind: str, value: int, version: int) -> VersionPin:
    return VersionPin(
        ref=ObjectRef(
            namespace="pu", type=kind, tenant_id=scope.tenant,
            id=TaggedId(kind="int", value=str(value)),
        ),
        version_kind="record_version",
        value=version,
    )


class SyntheticCorpusContentPipeline:
    """Orchestrate only existing A/B/C writers for a synthetic local input."""

    def __init__(self, *, source, context, claims):
        self.source = source
        self.context = context
        self.claims = claims

    def analyse(
        self,
        db,
        *,
        scope: RequestScope,
        message_ref: ObjectRef,
        message_source: VersionPin,
        message_version: VersionPin,
        message_bytes: StrictBytes,
        attachment_source: VersionPin,
        attachment_version: VersionPin,
        attachment_bytes: StrictBytes,
        evidence_ids: tuple[StrictStr, StrictStr],
        claim_anchor: ObjectRef,
        active_project_id: StrictInt,
    ) -> ContentPipelineResult:
        if not db.in_transaction():
            raise ContentExtractionError("caller_transaction_required")
        require_same_tenant(
            scope.tenant, message_ref, message_source.ref, message_version.ref,
            attachment_source.ref, attachment_version.ref, claim_anchor,
        )
        if (message_ref.type != "message" or message_source.ref.type != "source"
                or attachment_source.ref.type != "source"
                or message_version.ref.type != "source_version"
                or attachment_version.ref.type != "source_version"
                or claim_anchor.type != "deadline_claim"
                or type(active_project_id) is not int or active_project_id <= 0
                or len(evidence_ids) != 2):
            raise ContentExtractionError("content_unavailable")

        extracted = extract_synthetic_content(message=message_bytes, attachment=attachment_bytes)
        if extracted.status != "ready":
            return ContentPipelineResult(
                status="manual_review_required",
                reason_code=extracted.reason_code,
                manual_review_required=True,
            )

        projects = list(db.scalars(select(Project).where(
            Project.organization_id == int(scope.tenant.value),
            Project.name == extracted.project_name,
            Project.archived_at.is_(None),
        )))
        if len(projects) != 1:
            return ContentPipelineResult(
                status="manual_review_required", reason_code="ambiguous_context",
                manual_review_required=True,
            )
        project = projects[0]
        contracts = list(db.scalars(select(Contract).where(
            Contract.project_id == project.id,
            Contract.number == extracted.contract_number,
        )))
        if len(contracts) != 1:
            return ContentPipelineResult(
                status="manual_review_required", reason_code="ambiguous_context",
                manual_review_required=True,
            )
        contract = contracts[0]
        project_pin = _record_pin(scope, "project", project.id, project.record_version)
        contract_pin = _record_pin(scope, "contract", contract.id, contract.record_version)

        source_by_kind = {
            "message": (message_source, message_version, evidence_ids[0]),
            "attachment": (attachment_source, attachment_version, evidence_ids[1]),
        }
        evidence = []
        for span in extracted.spans:
            source_pin, version_pin, evidence_id = source_by_kind[span.source_kind]
            evidence.append(self.source.create_text_evidence(
                db,
                scope=scope,
                source=source_pin.ref,
                version=version_pin,
                evidence_id=evidence_id,
                char_start=span.start,
                char_end=span.end,
                confidence=span.confidence,
            ))
        evidence_pins = tuple(sorted(evidence, key=lambda item: canonical_json(item.model_dump(mode="json"))))
        relations = self.context.propose(
            db,
            scope=scope,
            message=message_ref,
            expected_context_version=1,
            project=project_pin,
            contract=contract_pin,
            evidence=evidence_pins,
        )
        claim = self.claims.extract(
            db,
            scope=scope,
            claim=DeadlineClaimInput(
                anchor=claim_anchor,
                revision=1,
                message=message_ref,
                due_date=extracted.due_date,
                timezone="Europe/Moscow",
                evidence=evidence_pins,
            ),
        )
        return ContentPipelineResult(
            status="awaiting_human_review",
            reason_code="separate_evidence_context_claim_reviews_required",
            manual_review_required=True,
            project=project_pin,
            contract=contract_pin,
            evidence=evidence_pins,
            relations=relations,
            claim=claim,
            due_date=extracted.due_date,
            active_project_selected=active_project_id == project.id,
        )
