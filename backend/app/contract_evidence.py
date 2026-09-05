"""Deterministic contract-term extraction with immutable v5.4 evidence bindings.

The module never reads provider data, never modifies a source document and never
commits a transaction.  A caller may apply high-confidence values only after
``persist_contract_evidence`` has bound every claim to the exact current
``SourceVersion`` and legacy ``DocumentVersion``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.models.document_version import DocumentVersion
from app.models.v54_pilot import (
    Evidence,
    EvidenceAssessment,
    SourceCurrent,
    SourceReference,
    SourceVersion,
)


_MONEY = re.compile(r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(?:руб(?:\.|лей|ля)?|₽)", re.I)
_PERCENT = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*%")
_DATE = re.compile(r"(?<!\d)([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})(?!\d)")
_PRICE_MARKER = re.compile(
    r"цена\s+(?:настоящего\s+)?договора|стоимость\s+(?:работ|услуг|договора)|общая\s+стоимость",
    re.I,
)
_RETENTION_MARKER = re.compile(r"удержан|удержива|гарантийн.*удерж", re.I)
_SIGNED_MARKER = re.compile(r"(?:договор|контракт).{0,120}\bот\s*", re.I)
_TERM_MARKER = re.compile(r"срок\s+(?:действия|исполнения)(?:\s+договора)?\s+(?:установлен\s+)?(?:до|по)", re.I)
_PARTY = re.compile(r"^(заказчик|подрядчик|исполнитель|поставщик)\s*:\s*(.+?)\s*[.;]?$", re.I)
_AUTO_CONFIDENCE = Decimal("0.95")


def _decimal(raw: str) -> Decimal:
    return Decimal(re.sub(r"\s+", "", raw).replace(",", "."))


def _date(match: re.Match[str]) -> date | None:
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def _proof(*, line: str, start: int, end: int, confidence: float, role: str | None = None) -> dict:
    value = {
        "locator": {
            "kind": "text_range",
            "unit": "unicode_codepoint",
            "start": start,
            "end": end,
        },
        "excerpt": line,
        "confidence": confidence,
        "confidence_kind": "deterministic_rule",
    }
    if role:
        value["derivation_role"] = role
    return value


def _candidate(value, proof: dict) -> dict:
    return {"value": value, "proof": proof}


def _canonical(value) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).casefold().strip()


def _resolve(candidates: list[dict], field: str, reasons: list[str]):
    if not candidates:
        return None, []
    values: dict[str, object] = {}
    for item in candidates:
        values.setdefault(_canonical(item["value"]), item["value"])
    proofs = [item["proof"] for item in candidates]
    if len(values) != 1:
        reasons.append(f"{field}_conflict")
        return None, proofs
    return next(iter(values.values())), proofs


def extract_contract_evidence(content: str) -> dict:
    """Extract explicit terms and exact code-point evidence without side effects."""
    if not isinstance(content, str) or not content or "\x00" in content:
        return {
            "status": "manual_review_required",
            "manual_review_required": True,
            "reason_codes": ["content_unavailable"],
            "field_evidence": {},
        }

    candidates: dict[str, list[dict]] = {
        key: [] for key in (
            "amount", "advance_amount", "advance_percent", "retention_percent",
            "signed_at", "term_until", "party_customer", "party_contractor",
        )
    }
    offset = 0
    for raw_line in content.splitlines(keepends=True):
        body = raw_line.rstrip("\r\n")
        leading = len(body) - len(body.lstrip())
        line = body.strip()
        start = offset + leading
        end = start + len(line)
        offset += len(raw_line)
        if not line:
            continue
        lowered = line.casefold()
        money = _MONEY.search(line)
        percent = _PERCENT.search(line)
        base = _proof(line=line, start=start, end=end, confidence=0.98)

        if money and _PRICE_MARKER.search(line):
            candidates["amount"].append(_candidate(_decimal(money.group(1)), base))
        if "аванс" in lowered:
            if money:
                candidates["advance_amount"].append(_candidate(_decimal(money.group(1)), base))
            if percent:
                candidates["advance_percent"].append(_candidate(_decimal(percent.group(1)), base))
        if percent and _RETENTION_MARKER.search(line):
            candidates["retention_percent"].append(_candidate(_decimal(percent.group(1)), base))

        date_match = _DATE.search(line)
        parsed_date = _date(date_match) if date_match else None
        if parsed_date and _SIGNED_MARKER.search(line):
            candidates["signed_at"].append(_candidate(parsed_date, base))
        if parsed_date and _TERM_MARKER.search(line):
            candidates["term_until"].append(_candidate(parsed_date, base))

        party = _PARTY.match(line)
        if party:
            role = party.group(1).casefold()
            field = "party_customer" if role == "заказчик" else "party_contractor"
            candidates[field].append(_candidate(party.group(2).strip(), _proof(
                line=line, start=start, end=end, confidence=0.96,
            )))

    reasons: list[str] = []
    values: dict[str, object] = {}
    evidence: dict[str, list[dict]] = {}
    for field, items in candidates.items():
        values[field], evidence[field] = _resolve(items, field, reasons)

    if values["advance_amount"] is None and values["advance_percent"] is not None and values["amount"] is not None:
        values["advance_amount"] = (
            Decimal(values["amount"]) * Decimal(values["advance_percent"]) / Decimal("100")
        ).quantize(Decimal("0.01"))
        evidence["advance_amount"] = [
            {**proof, "confidence": 0.94, "derivation_role": "contract_amount"}
            for proof in evidence["amount"]
        ] + [
            {**proof, "confidence": 0.94, "derivation_role": "advance_percent"}
            for proof in evidence["advance_percent"]
        ]

    for field, value in values.items():
        if value is not None and evidence[field] and min(
            Decimal(str(item["confidence"])) for item in evidence[field]
        ) < _AUTO_CONFIDENCE:
            reasons.append(f"{field}_low_confidence")

    parties = {
        "customer": values["party_customer"],
        "contractor": values["party_contractor"],
    }
    if not any(value is not None for value in values.values()):
        reasons.append("no_contract_terms_extracted")
    manual = bool(reasons)
    result = {
        "status": "manual_review_required" if manual else "ready",
        "manual_review_required": manual,
        "reason_codes": sorted(set(reasons)),
        "amount": values["amount"],
        "advance_amount": values["advance_amount"],
        "advance_percent": values["advance_percent"],
        "retention_percent": values["retention_percent"],
        "signed_at": values["signed_at"],
        "term_until": values["term_until"],
        "parties": parties,
        "field_evidence": evidence,
        # Compatibility excerpts for existing UI/mismatch rendering.
        "amount_evidence": evidence["amount"][0]["excerpt"] if evidence["amount"] else None,
        "advance_evidence": evidence["advance_amount"][0]["excerpt"] if evidence["advance_amount"] else None,
        "retention_evidence": evidence["retention_percent"][0]["excerpt"] if evidence["retention_percent"] else None,
    }
    return result


def _evidence_id(version_id: str, field: str, proof: dict, value) -> str:
    locator = proof["locator"]
    seed = ":".join((
        version_id,
        field,
        str(locator["start"]),
        str(locator["end"]),
        _canonical(value),
        str(proof.get("derivation_role") or "direct"),
    ))
    return str(uuid5(NAMESPACE_URL, f"pu-workspace:contract-evidence:{seed}"))


def persist_contract_evidence(
    db,
    *,
    organization_id: int,
    project_id: int,
    document_version: DocumentVersion,
    extraction: dict,
) -> dict:
    """Persist evidence only when the exact document version has one current source pin."""
    versions = list(db.scalars(
        select(SourceVersion)
        .join(SourceReference, SourceReference.id == SourceVersion.source_id)
        .join(SourceCurrent, SourceCurrent.version_id == SourceVersion.id)
        .where(
            SourceVersion.organization_id == organization_id,
            SourceVersion.legacy_document_version_id == document_version.id,
            SourceReference.organization_id == organization_id,
            SourceReference.origin_project_id == project_id,
            SourceReference.availability == "available",
            SourceCurrent.organization_id == organization_id,
            SourceCurrent.source_id == SourceVersion.source_id,
        )
    ))
    if len(versions) != 1:
        return {
            "status": "manual_review_required",
            "manual_review_required": True,
            "reason_codes": ["exact_source_version_unavailable"],
            "document_version_id": document_version.id,
            "source_id": None,
            "source_version_id": None,
            "evidence": [],
        }
    version = versions[0]
    source = db.get(SourceReference, version.source_id)
    if (
        source is None
        or not source.policy_pins
        or version.consistency not in {"revision_bound", "digest_observed"}
    ):
        return {
            "status": "manual_review_required",
            "manual_review_required": True,
            "reason_codes": ["exact_source_version_unavailable"],
            "document_version_id": document_version.id,
            "source_id": None,
            "source_version_id": None,
            "evidence": [],
        }

    plans: list[dict] = []
    for field, proofs in extraction.get("field_evidence", {}).items():
        value = extraction.get(field)
        if field.startswith("party_"):
            value = extraction.get("parties", {}).get(field.removeprefix("party_"))
        if value is None:
            # Conflicting candidates are still useful review evidence.  Bind the
            # individual proof to a non-content candidate ordinal, never a quote.
            values = [f"candidate-{index + 1}" for index in range(len(proofs))]
        else:
            values = [value] * len(proofs)
        for index, proof in enumerate(proofs):
            evidence_id = _evidence_id(version.id, field, proof, values[index])
            expected_locator = dict(proof["locator"])
            expected_extractor = {
                "name": "contract_terms_local",
                "version": "1",
                "method": "deterministic_rule",
                "field": field,
                "derivation_role": proof.get("derivation_role"),
            }
            plans.append({
                "field": field,
                "evidence_id": evidence_id,
                "locator": expected_locator,
                "extractor": expected_extractor,
                "confidence": float(proof["confidence"]),
                "confidence_kind": proof["confidence_kind"],
            })

    # Validate every deterministic identity before adding any row.  A collision
    # or attempted rebinding cannot leave a partially persisted evidence set.
    for plan in plans:
        row = db.get(Evidence, plan["evidence_id"])
        assessment = db.get(EvidenceAssessment, plan["evidence_id"]) if row else None
        if row is not None and (
            row.organization_id != organization_id
            or row.source_id != source.id
            or row.source_version_id != version.id
            or row.locator != plan["locator"]
            or row.extractor != plan["extractor"]
            or row.confidence != plan["confidence"]
            or row.confidence_kind != plan["confidence_kind"]
            or row.policy_pins != source.policy_pins
            or assessment is None
            or assessment.organization_id != organization_id
        ):
            return {
                "status": "manual_review_required",
                "manual_review_required": True,
                "reason_codes": ["immutable_evidence_conflict"],
                "document_version_id": document_version.id,
                "source_id": source.id,
                "source_version_id": version.id,
                "evidence": [],
            }

    persisted: list[dict] = []
    for plan in plans:
        row = db.get(Evidence, plan["evidence_id"])
        assessment = db.get(EvidenceAssessment, plan["evidence_id"]) if row else None
        if row is None:
            row = Evidence(
                id=plan["evidence_id"],
                organization_id=organization_id,
                source_id=source.id,
                source_version_id=version.id,
                locator=plan["locator"],
                extractor=plan["extractor"],
                confidence=plan["confidence"],
                confidence_kind=plan["confidence_kind"],
                extracted_at=version.observed_at,
                policy_pins=source.policy_pins,
            )
            db.add(row)
            db.flush()
            assessment = EvidenceAssessment(
                evidence_id=row.id,
                organization_id=organization_id,
                verification="unverified",
                freshness=source.freshness,
                availability=source.availability,
                checked_at=source.last_checked_at,
                valid_until=source.next_check_at,
            )
            db.add(assessment)
            db.flush()
        persisted.append({
            "field": plan["field"],
            "evidence_id": row.id,
            "evidence_revision": row.revision,
            "source_id": source.id,
            "source_version_id": version.id,
            "document_version_id": document_version.id,
            "locator": plan["locator"],
            "confidence": row.confidence,
            "verification": assessment.verification,
        })

    reasons = list(extraction.get("reason_codes", []))
    return {
        "status": extraction.get("status", "manual_review_required"),
        "manual_review_required": bool(extraction.get("manual_review_required", True)),
        "reason_codes": reasons,
        "document_version_id": document_version.id,
        "source_id": source.id,
        "source_version_id": version.id,
        "evidence": persisted,
    }
