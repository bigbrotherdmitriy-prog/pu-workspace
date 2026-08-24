from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.organizer_engine import DriveClient, OrganizerExecutor, OrganizerRepository, build_proposal
from app.organizer_engine.drive_factory import get_drive_service
from app.organizer_engine.types import DriveFile

router = APIRouter(prefix="/organizer", tags=["organizer"])


class AnalyzeRequest(BaseModel):
    project_id: int
    folder_name: str = Field(min_length=1, max_length=500)
    files: list[str] = Field(default_factory=list)


class ScanRequest(BaseModel):
    project_id: int
    source_folder_id: str = Field(min_length=3)
    background: bool = True


class DecisionRequest(BaseModel):
    approved: bool
    note: Optional[str] = None


class EditItemRequest(BaseModel):
    decision: str = Field(pattern="^(approved|edited|skipped|pending)$")
    edited_name: Optional[str] = None
    edited_folder: Optional[str] = None
    save_as_rule: bool = False


class RuleRequest(BaseModel):
    filename_contains: str
    folder: str
    confirmed: bool = True


def _proposal_payload(repo: OrganizerRepository, proposal_id: int):
    p = repo.proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return {**dict(p), "actions": [dict(x) for x in repo.proposal_items(proposal_id)]}


def _scan_worker(session_id: int, project_id: int, source_folder_id: str):
    db = SessionLocal()
    repo = OrganizerRepository(db)
    try:
        repo.update_session(session_id, status="scanning", progress=5)
        project = repo.project(project_id)
        if not project:
            raise ValueError("Project not found")
        service = get_drive_service(project_id=project_id, db=db)
        drive = DriveClient(service)
        source = drive.get_file_meta(source_folder_id)
        if not source.is_folder:
            raise ValueError("Source ID is not a Google Drive folder")
        source_items = drive.walk_tree(source_folder_id)
        repo.update_session(session_id, source_item_count=len(source_items), progress=15)
        copy_result = drive.copy_folder_tree(
            source_folder_id,
            source.parent_id,
            source.name,
            source_items=source_items,
        )
        repo.update_session(
            session_id,
            copy_folder_id=copy_result.copy_root_id,
            copy_folder_name=copy_result.copy_root_name,
            copy_item_count=copy_result.item_count,
            status="analyzing",
            progress=55,
        )
        copy_items = drive.walk_tree(copy_result.copy_root_id)
        rules = repo.confirmed_rules()
        items = build_proposal(copy_items, project_name=project["name"], confirmed_rules=rules)
        proposal_id = repo.create_proposal(
            project_id, session_id, source.name, source_folder_id, copy_result.copy_root_id
        )
        repo.save_items(proposal_id, items)
        repo.update_session(session_id, status="proposed", progress=100)
    except Exception as exc:
        db.rollback()
        try:
            repo.update_session(session_id, status="failed", error_message=str(exc), progress=100)
        except Exception:
            pass
    finally:
        db.close()


@router.get("/status")
def status():
    return {
        "status": "ok",
        "module": "organizer",
        "mode": "preview-first",
        "storage": "postgresql",
        "safe_copy_required": True,
        "originals_modified": False,
    }


@router.post("/scan")
def scan(payload: ScanRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    project = repo.project(payload.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    # Resolve metadata before creating the job so invalid IDs fail fast.
    try:
        drive = DriveClient(get_drive_service(project_id=payload.project_id, db=db))
        source = drive.get_file_meta(payload.source_folder_id)
        if not source.is_folder:
            raise ValueError("Source ID is not a folder")
        if "(безопасная копия " in source.name:
            raise ValueError(
                "Refusing to scan a safe-copy folder. Select the original source folder."
            )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    session_id = repo.create_session(payload.project_id, source.id, source.name)
    db.commit()
    if payload.background:
        background_tasks.add_task(_scan_worker, session_id, payload.project_id, source.id)
        return {"session_id": session_id, "status": "queued"}
    _scan_worker(session_id, payload.project_id, source.id)
    return dict(repo.get_session(session_id) or {})


@router.get("/sessions/{session_id}")
def session_status(session_id: int, db: Session = Depends(get_db)):
    row = OrganizerRepository(db).get_session(session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return dict(row)


@router.post("/analyze")
def compatibility_analyze(payload: AnalyzeRequest, db: Session = Depends(get_db)):
    """Compatibility endpoint: proposal-only, no Drive mutation/copy.

    Kept for existing clients/tests. Real MVP flow should use /scan because TZ
    requires a separate safe copy before applying changes.
    """
    repo = OrganizerRepository(db)
    project = repo.project(payload.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    session_id = repo.create_session(payload.project_id, "manual", payload.folder_name)
    repo.update_session(session_id, status="proposed", progress=100, source_item_count=len(payload.files))
    fake = [DriveFile(str(i), name, "application/octet-stream", "manual") for i, name in enumerate(payload.files, 1)]
    proposal_id = repo.create_proposal(payload.project_id, session_id, payload.folder_name, "manual", "manual")
    repo.save_items(proposal_id, build_proposal(fake, project["name"], repo.confirmed_rules()))
    return _proposal_payload(repo, proposal_id)


@router.get("/proposals")
def proposals(project_id: int | None = None, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    sql = "SELECT id FROM organizer_proposals"
    params = {}
    if project_id is not None:
        sql += " WHERE project_id=:p"; params["p"] = project_id
    sql += " ORDER BY id DESC LIMIT 100"
    from sqlalchemy import text
    ids = [x[0] for x in db.execute(text(sql), params).all()]
    return {"proposals": [_proposal_payload(repo, i) for i in ids], "count": len(ids)}


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: int, db: Session = Depends(get_db)):
    return _proposal_payload(OrganizerRepository(db), proposal_id)


@router.patch("/actions/{action_id}")
def edit_action(action_id: int, payload: EditItemRequest, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    row = db.execute(__import__("sqlalchemy").text("SELECT * FROM organizer_actions WHERE id=:id"), {"id": action_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Action not found")
    repo.edit_item(action_id, payload.decision, payload.edited_name, payload.edited_folder)
    if payload.save_as_rule and payload.edited_folder:
        repo.add_rule({"filename_contains": row["source"]}, {"folder": payload.edited_folder}, None, "user_correction", True)
        db.commit()
    return {"status": "ok", "action_id": action_id}


@router.post("/proposals/{proposal_id}/decision")
def decide(proposal_id: int, payload: DecisionRequest, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    p = repo.proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p["status"] != "waiting_confirmation":
        raise HTTPException(409, "Proposal already processed")
    repo.decide(proposal_id, payload.approved, payload.note)
    return _proposal_payload(repo, proposal_id)


@router.post("/proposals/{proposal_id}/apply")
def apply(proposal_id: int, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    p = repo.proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if not p["copy_folder_id"] or p["copy_folder_id"] == "manual":
        raise HTTPException(409, "Apply requires a real Google Drive safe copy created by /scan")
    if p["status"] != "approved":
        raise HTTPException(409, "Proposal must be approved before apply")
    try:
        drive = DriveClient(get_drive_service(project_id=p["project_id"], db=db))
        repo.mark_prepared(proposal_id)
        stats = OrganizerExecutor(repo, drive).apply(proposal_id)
        return {"proposal": _proposal_payload(repo, proposal_id), "stats": stats}
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/proposals/{proposal_id}/rollback")
def rollback(proposal_id: int, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    p = repo.proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    try:
        drive = DriveClient(get_drive_service(project_id=p["project_id"], db=db))
        return OrganizerExecutor(repo, drive).rollback(proposal_id)
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/rules")
def create_rule(payload: RuleRequest, db: Session = Depends(get_db)):
    repo = OrganizerRepository(db)
    rule_id = repo.add_rule(
        {"filename_contains": payload.filename_contains},
        {"folder": payload.folder},
        None,
        "manual",
        payload.confirmed,
    )
    db.commit()
    return {"id": rule_id, "confirmed": payload.confirmed}
