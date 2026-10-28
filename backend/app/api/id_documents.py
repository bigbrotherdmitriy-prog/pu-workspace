from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.id_documents import (
    AosrData,
    Material,
    OrgRequisites,
    Representative,
    aosr_missing,
    render_aosr_docx,
)
from app.models.executive_docs import IdParticipant
from app.models.organization_contract import Contract, Organization
from app.models.project import Project

router = APIRouter(prefix="/projects", tags=["id-documents"])


class ParticipantCreate(BaseModel):
    role: str
    full_name: str
    object_name: str | None = None
    position: str | None = None
    order_ref: str | None = None
    nrs_id: str | None = None
    org_name: str | None = None
    org_ogrn: str | None = None
    org_inn: str | None = None
    org_address: str | None = None


class ParticipantUpdate(BaseModel):
    role: str | None = None
    full_name: str | None = None
    object_name: str | None = None
    position: str | None = None
    order_ref: str | None = None
    nrs_id: str | None = None
    org_name: str | None = None
    org_ogrn: str | None = None
    org_inn: str | None = None
    org_address: str | None = None


class MaterialPayload(BaseModel):
    name: str
    quality_doc: str | None = None


class AosrPayload(BaseModel):
    contract_id: int
    object_name: str
    number: str
    act_date: date | None = None
    works: str
    design_docs: str | None = None
    materials: list[MaterialPayload] = []
    supporting_docs: list[str] = []
    work_start: date | None = None
    work_finish: date | None = None
    norms: list[str] = []
    next_works: str | None = None
    object_address: str | None = None


def _participant_dict(p: IdParticipant) -> dict:
    return {
        "id": p.id,
        "project_id": p.project_id,
        "object_name": p.object_name,
        "role": p.role,
        "position": p.position,
        "full_name": p.full_name,
        "order_ref": p.order_ref,
        "nrs_id": p.nrs_id,
        "org_name": p.org_name,
        "org_ogrn": p.org_ogrn,
        "org_inn": p.org_inn,
        "org_address": p.org_address,
    }


def _org_from_organization(org: Organization) -> OrgRequisites:
    return OrgRequisites(
        name=org.name,
        ogrn=org.ogrn,
        inn=org.inn,
        address=org.legal_address,
        phone=org.phone,
    )


def _participant_to_representative(p: IdParticipant) -> Representative:
    organization: Optional[OrgRequisites] = None
    if p.org_name is not None:
        organization = OrgRequisites(
            name=p.org_name,
            ogrn=p.org_ogrn,
            inn=p.org_inn,
            address=p.org_address,
        )
    return Representative(
        role=p.role,
        position=p.position,
        full_name=p.full_name,
        order_ref=p.order_ref,
        nrs_id=p.nrs_id,
        organization=organization,
    )


def _build_aosr_data(db: Session, project_id: int, body: AosrPayload) -> AosrData:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    contract = db.query(Contract).filter(
        Contract.id == body.contract_id, Contract.project_id == project_id,
    ).first()
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")

    developer_org: OrgRequisites | None = None
    if contract.counterparty_organization_id is not None:
        org = db.query(Organization).filter(
            Organization.id == contract.counterparty_organization_id,
        ).first()
        if org is not None:
            developer_org = _org_from_organization(org)
    if developer_org is None:
        developer_org = OrgRequisites(name=contract.counterparty or "")

    builder_org: OrgRequisites
    if project.organization_id is not None:
        proj_org = db.query(Organization).filter(
            Organization.id == project.organization_id,
        ).first()
        if proj_org is not None:
            builder_org = _org_from_organization(proj_org)
        else:
            builder_org = OrgRequisites(name="")
    else:
        builder_org = OrgRequisites(name="")

    participants = (
        db.query(IdParticipant)
        .filter(
            IdParticipant.project_id == project_id,
            (IdParticipant.object_name == body.object_name)
            | (IdParticipant.object_name.is_(None)),
        )
        .all()
    )
    representatives = [_participant_to_representative(p) for p in participants]

    materials = [
        Material(name=m.name, quality_doc=m.quality_doc) for m in body.materials
    ]

    designer_org: OrgRequisites | None = None
    for p in participants:
        if p.role == "designer" and p.org_name is not None:
            designer_org = OrgRequisites(
                name=p.org_name,
                ogrn=p.org_ogrn,
                inn=p.org_inn,
                address=p.org_address,
            )
            break

    return AosrData(
        object_name=body.object_name,
        object_address=body.object_address,
        number=body.number,
        act_date=body.act_date,
        developer=developer_org,
        builder=builder_org,
        designer=designer_org,
        representatives=representatives,
        works=body.works,
        design_docs=body.design_docs,
        materials=materials,
        supporting_docs=list(body.supporting_docs),
        work_start=body.work_start,
        work_finish=body.work_finish,
        norms=list(body.norms),
        next_works=body.next_works,
    )


@router.get("/{project_id}/executive-docs/participants")
def list_participants(
    project_id: int,
    object_name: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    q = db.query(IdParticipant).filter(IdParticipant.project_id == project_id)
    if object_name is not None:
        q = q.filter(
            (IdParticipant.object_name == object_name)
            | (IdParticipant.object_name.is_(None)),
        )
    participants = q.all()
    return [_participant_dict(p) for p in participants]


@router.post("/{project_id}/executive-docs/participants")
def create_participant(
    project_id: int,
    body: ParticipantCreate,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    participant = IdParticipant(
        project_id=project_id,
        object_name=body.object_name,
        role=body.role,
        position=body.position,
        full_name=body.full_name,
        order_ref=body.order_ref,
        nrs_id=body.nrs_id,
        org_name=body.org_name,
        org_ogrn=body.org_ogrn,
        org_inn=body.org_inn,
        org_address=body.org_address,
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return _participant_dict(participant)


@router.patch("/{project_id}/executive-docs/participants/{participant_id}")
def update_participant(
    project_id: int,
    participant_id: int,
    body: ParticipantUpdate,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    participant = db.query(IdParticipant).filter(
        IdParticipant.id == participant_id,
        IdParticipant.project_id == project_id,
    ).first()
    if participant is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    for field_name, value in body.model_dump(exclude_unset=True).items():
        setattr(participant, field_name, value)
    db.commit()
    db.refresh(participant)
    return _participant_dict(participant)


@router.delete("/{project_id}/executive-docs/participants/{participant_id}")
def delete_participant(
    project_id: int,
    participant_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    participant = db.query(IdParticipant).filter(
        IdParticipant.id == participant_id,
        IdParticipant.project_id == project_id,
    ).first()
    if participant is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    db.delete(participant)
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/executive-docs/aosr/check")
def check_aosr(
    project_id: int,
    body: AosrPayload,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    data = _build_aosr_data(db, project_id, body)
    return {"missing": aosr_missing(data)}


@router.post("/{project_id}/executive-docs/aosr/docx")
def download_aosr_docx(
    project_id: int,
    body: AosrPayload,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    if body.work_start is None or body.work_finish is None:
        raise HTTPException(
            status_code=422,
            detail="Укажите даты начала и окончания работ (п. 5 акта)",
        )
    if body.work_finish < body.work_start:
        raise HTTPException(
            status_code=422,
            detail="Дата окончания работ раньше даты начала",
        )
    data = _build_aosr_data(db, project_id, body)
    payload = render_aosr_docx(data)
    ascii_number = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "-_")) else "_"
        for ch in body.number
    ) or "_"
    filename = f"AOSR-{ascii_number}.docx"
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
