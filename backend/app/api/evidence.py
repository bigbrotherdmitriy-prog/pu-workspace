"""Read-only product endpoint for exact, server-authorized evidence fragments."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.observability import request_id_context
from app.core.v54_interfaces import RequestScope
from app.core.v54_permissions import SourceEvidenceError
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.database import get_db
from app.models.user import User
from app.models.v54_pilot import Evidence, SourceReference
from app.source_evidence.fragment_reader import FragmentLimits, read_fragment
from app.source_evidence.http_projection import readable_payload, unavailable_payload
from app.source_evidence.product import ProductEvidenceResolver, fragment_store_from_app, utcnow


router = APIRouter(prefix="/api/v54/evidence", tags=["v54-evidence"])
_NO_CACHE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def get_evidence_clock():
    return utcnow


def _unavailable():
    return JSONResponse(status_code=404, content=unavailable_payload(), headers=_NO_CACHE)


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
