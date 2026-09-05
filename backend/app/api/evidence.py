"""Read-only product endpoint for exact, server-authorized evidence fragments."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.core.observability import request_id_context
from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.database import get_db
from app.models.document_version import DocumentVersion
from app.models.user import User
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.source_evidence.fragment_reader import FragmentLimits, read_fragment
from app.source_evidence.http_projection import readable_payload, unavailable_payload
from app.source_evidence.product import ProductEvidenceResolver, fragment_store_from_app, utcnow


router = APIRouter(prefix="/api/v54/evidence", tags=["v54-evidence"])
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def get_evidence_clock():
    return utcnow


def _unavailable():
    return JSONResponse(status_code=404, content=unavailable_payload(), headers=_NO_CACHE)


def _selection_locator(locator: object) -> dict:
    """Return navigation-only metadata, never excerpts or provider locators."""
    if not isinstance(locator, dict):
        return {"kind": "unavailable"}
    kind = locator.get("kind")
    if kind in {"page", "page_bbox", "page_region"}:
        page = locator.get("page")
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            return {"kind": "page", "page": page}
    return {"kind": "exact_fragment"}


@router.get("")
def list_current_project_evidence(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Safe selector catalog for exact current evidence in one authorized project."""
    require_project_role(db, user, project_id, "viewer")
    rows = db.execute(
        select(Evidence, EvidenceAssessment, SourceVersion, DocumentVersion)
        .join(SourceVersion, and_(
            SourceVersion.organization_id == Evidence.organization_id,
            SourceVersion.source_id == Evidence.source_id,
            SourceVersion.id == Evidence.source_version_id,
        ))
        .join(SourceReference, and_(
            SourceReference.organization_id == SourceVersion.organization_id,
            SourceReference.id == SourceVersion.source_id,
        ))
        .join(SourceCurrent, and_(
            SourceCurrent.organization_id == SourceVersion.organization_id,
            SourceCurrent.source_id == SourceVersion.source_id,
            SourceCurrent.version_id == SourceVersion.id,
        ))
        .join(EvidenceAssessment, and_(
            EvidenceAssessment.organization_id == Evidence.organization_id,
            EvidenceAssessment.evidence_id == Evidence.id,
        ))
        .join(DocumentVersion, DocumentVersion.id == SourceVersion.legacy_document_version_id)
        .where(
            SourceReference.origin_project_id == project_id,
            SourceReference.availability == "available",
            EvidenceAssessment.availability == "available",
            EvidenceAssessment.freshness == "fresh",
            EvidenceAssessment.valid_until.is_not(None),
            EvidenceAssessment.valid_until > datetime.now(timezone.utc),
        )
        .order_by(DocumentVersion.id.desc(), Evidence.id)
    ).all()
    items = [
        {
            "evidenceId": evidence.id,
            "evidenceRevision": evidence.revision,
            "sourceVersionId": source_version.id,
            "documentVersionId": document_version.id,
            "assessmentVersion": assessment.record_version,
            "verification": assessment.verification,
            "confidence": evidence.confidence,
            "locator": _selection_locator(evidence.locator),
        }
        for evidence, assessment, source_version, document_version in rows
    ]
    return {"projectId": project_id, "items": items, "total": len(items)}


@router.get("/{evidence_id}/fragment")
def evidence_fragment(
    evidence_id: str,
    revision: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    clock=Depends(get_evidence_clock),
):
    try:
        if revision != 1 or str(UUID(evidence_id)) != evidence_id:
            raise SourceEvidenceError("resource_unavailable")
        owner = db.execute(
            select(Evidence.organization_id, SourceReference.origin_project_id)
            .join(SourceReference, and_(
                SourceReference.id == Evidence.source_id,
                SourceReference.organization_id == Evidence.organization_id,
            ))
            .where(Evidence.id == evidence_id, Evidence.revision == revision)
        ).one_or_none()
        if owner is None:
            raise SourceEvidenceError("resource_unavailable")
        tenant_id, project_id = owner
        tenant = TaggedId(kind="int", value=str(tenant_id))
        scope = RequestScope(
            tenant=tenant,
            actor=ObjectRef(
                namespace="pu", type="user", tenant_id=tenant,
                id=TaggedId(kind="int", value=str(user.id)),
            ),
            project=ObjectRef(
                namespace="pu", type="project", tenant_id=tenant,
                id=TaggedId(kind="int", value=str(project_id)),
            ),
            correlation_id=request_id_context.get() or "evidence-read",
        )
        pin = VersionPin(
            ref=ObjectRef(
                namespace="pu", type="evidence", tenant_id=tenant,
                id=TaggedId(kind="uuid", value=evidence_id),
            ),
            version_kind="revision",
            value=revision,
        )
        result = read_fragment(
            db,
            scope=scope,
            evidence_pin=pin,
            resolver=ProductEvidenceResolver(clock=clock),
            store=fragment_store_from_app(request),
            clock=clock,
            limits=FragmentLimits(),
        )
        payload = readable_payload(db, result)
    except Exception:
        return _unavailable()
    response.headers.update(_NO_CACHE)
    return payload
