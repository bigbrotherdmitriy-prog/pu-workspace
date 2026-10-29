from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.phase_zero_protocol import to_xlsx


router = APIRouter(prefix="/projects", tags=["phase-zero-protocol"])


class PhaseZeroProtocolBody(BaseModel):
    header: dict[str, str | None]
    rows: list[dict]


@router.post("/{project_id}/executive-docs/phase-zero-protocol.xlsx")
def build_phase_zero_protocol(
    project_id: int,
    body: PhaseZeroProtocolBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = to_xlsx(body.header, body.rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    protocol_no = body.header.get("protocol_no") or "protocol"
    filename = f"phase-zero-protocol-{protocol_no}.xlsx"
    encoded = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
    }
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers=headers,
    )
