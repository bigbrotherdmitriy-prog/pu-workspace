from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.avr_protocol import to_xlsx
from app.core.auth import require_project_role, require_user
from app.database import get_db


router = APIRouter(prefix="/projects", tags=["avr-protocol"])


class AvrProtocolBody(BaseModel):
    header: dict[str, str | None]
    rows: list[dict]


@router.post("/{project_id}/executive-docs/avr-protocol.xlsx")
def build_avr_protocol(
    project_id: int,
    body: AvrProtocolBody,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    try:
        data = to_xlsx(body.header, body.rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    protocol_no = body.header.get("protocol_no") or "protocol"
    filename = f"avr-protocol-{protocol_no}.xlsx"
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
