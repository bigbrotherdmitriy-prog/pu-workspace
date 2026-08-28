from __future__ import annotations

from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.organizer_engine import DriveClient, OrganizerExecutor, OrganizerRepository, build_proposal
from app.organizer_engine.drive_factory import get_drive_service
from app.organizer_engine.types import DriveFile
from app.organizer_engine.config import AUTO_APPLY_CONFIDENCE, AUTO_APPLY_ENABLED, FOLDER_STRUCTURE
from app.core.auth import require_admin, require_project_role, require_user
from app.integrations.telegram import notify_telegram
from app.models.user import User
from app.models.audit_log import AuditLog
from app.task_engine import create_tasks_from_files
from app.response_engine import create_response_drafts
from app.governance_engine import create_governance_items
from app.document_engine import index_documents

router = APIRouter(prefix="/organizer", tags=["organizer"])
_workers = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("ORGANIZER_WORKERS", "2"))))


def _audit(db: Session, action: str, entity_type: str, entity_id: int, details: str) -> None:
    db.add(AuditLog(action=action, entity_type=entity_type, entity_id=entity_id, details=details))
    db.commit()


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


class SourceApplyRequest(BaseModel):
    action_id: int
    confirmation: str


def _proposal_payload(repo: OrganizerRepository, proposal_id: int):
    p = repo.proposal(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return {**dict(p), "actions": [dict(x) for x in repo.proposal_items(proposal_id)]}


def _session_for_user(repo, db, user, session_id, minimum="viewer"):
    row = repo.get_session(session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    require_project_role(db, user, row["project_id"], minimum)
    return row


def _proposal_for_user(repo, db, user, proposal_id, minimum="viewer"):
    row = repo.proposal(proposal_id)
    if not row:
        raise HTTPException(404, "Proposal not found")
    require_project_role(db, user, row["project_id"], minimum)
    return row


def _scan_worker(
    session_id: int,
    project_id: int,
    source_folder_id: str,
    auto_apply: bool | None = None,
):
    db = SessionLocal()
    repo = OrganizerRepository(db)
    try:
        session = repo.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        if repo.proposal_for_session(session_id):
            repo.update_session(session_id, status="proposed", progress=100)
            return
        project = repo.project(project_id)
        if not project:
            raise ValueError("Project not found")
        service = get_drive_service(project_id=project_id, db=db)
        drive = DriveClient(service)
        if session["copy_folder_id"]:
            copy_folder_id = session["copy_folder_id"]
            source_name = session["source_folder_name"]
            repo.update_session(session_id, status="analyzing", progress=max(55, session["progress"] or 0))
        else:
            repo.update_session(session_id, status="scanning", progress=5)
            source = drive.get_file_meta(source_folder_id)
            if not source.is_folder:
                raise ValueError("Source ID is not a Google Drive folder")
            source_name = source.name
            source_items = drive.walk_tree(source_folder_id)
            repo.update_session(session_id, source_item_count=len(source_items), progress=15)
            copy_result = drive.copy_folder_tree(
                source_folder_id, source.parent_id, source.name, source_items=source_items,
            )
            copy_folder_id = copy_result.copy_root_id
            repo.update_session(
                session_id, copy_folder_id=copy_folder_id,
                copy_folder_name=copy_result.copy_root_name,
                copy_item_count=copy_result.item_count, status="analyzing", progress=55,
            )
        copy_items = drive.walk_tree(copy_folder_id)
        drive.populate_content(copy_items)
        index_documents(db, project_id, copy_items, "google_drive_copy")
        rules = repo.confirmed_rules()
        items = build_proposal(copy_items, project_name=project["name"], confirmed_rules=rules)
        tasks = create_tasks_from_files(db, project_id, session_id, copy_items)
        google_synced = calendar_synced = 0
        drafts = create_response_drafts(db, project_id, session_id, copy_items)
        risks, decisions = create_governance_items(db, project_id, copy_items)
        proposal_id = repo.create_proposal(
            project_id, session_id, source_name, source_folder_id, copy_folder_id
        )
        repo.save_items(proposal_id, items)
        if AUTO_APPLY_ENABLED if auto_apply is None else auto_apply:
            approved = repo.apply_auto_policy(proposal_id, AUTO_APPLY_CONFIDENCE)
            if approved:
                if not repo.mark_prepared(proposal_id):
                    raise ValueError("Automatic proposal could not be prepared")
                OrganizerExecutor(repo, drive).apply(proposal_id)
                repo.update_session(session_id, status="applied", progress=100)
                notify_telegram(
                    f"PU Workspace: «{source_name}» обработана автоматически. "
                    f"Применено безопасных действий: {approved}. Оригиналы не изменялись. "
                    f"Задач: {len(tasks)}; Google Tasks: {google_synced}; Calendar: {calendar_synced}. "
                    f"Рисков: {len(risks)}; решений на подтверждение: {len(decisions)}; черновиков ответов: {len(drafts)}."
                )
            else:
                repo.update_session(session_id, status="proposed", progress=100)
                notify_telegram(
                    f"PU Workspace: анализ «{source_name}» завершён. "
                    f"Безопасных автоматических действий нет; требуется проверка. "
                    f"Задач: {len(tasks)}; Google Tasks: {google_synced}; Calendar: {calendar_synced}. "
                    f"Рисков: {len(risks)}; решений на подтверждение: {len(decisions)}; черновиков ответов: {len(drafts)}."
                )
        else:
            repo.update_session(session_id, status="proposed", progress=100)
            notify_telegram(
                f"PU Workspace: предложение для «{source_name}» готово к проверке. "
                f"Задач: {len(tasks)}; Google Tasks: {google_synced}; Calendar: {calendar_synced}. "
                f"Рисков: {len(risks)}; решений на подтверждение: {len(decisions)}; черновиков ответов: {len(drafts)}."
            )
    except Exception as exc:
        db.rollback()
        try:
            failed_session = repo.get_session(session_id)
            failed_status = "dead_letter" if failed_session and failed_session["retry_count"] >= 2 else "failed"
            repo.update_session(session_id, status=failed_status, error_message=str(exc), progress=100)
            notify_telegram(f"PU Workspace: ошибка обработки сессии {session_id}: {str(exc)[:500]}")
        except Exception:
            pass
    finally:
        db.close()


def submit_scan(session_id: int, project_id: int, source_folder_id: str) -> None:
    _workers.submit(_scan_worker, session_id, project_id, source_folder_id)


def recover_incomplete_scans() -> int:
    db = SessionLocal()
    try:
        rows = list(OrganizerRepository(db).incomplete_sessions())
    finally:
        db.close()
    for row in rows:
        submit_scan(row["id"], row["project_id"], row["source_folder_id"])
    return len(rows)


@router.get("/status")
def status():
    return {
        "status": "ok",
        "module": "organizer",
        "mode": "auto-copy" if AUTO_APPLY_ENABLED else "preview-first",
        "storage": "postgresql",
        "safe_copy_required": True,
        "originals_modified": False,
    }


@router.post("/scan")
def scan(payload: ScanRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_user)):
    require_project_role(db, user, payload.project_id, "manager")
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
    _audit(db, "snapshot_scan_started", "organizer_session", session_id, f"Source folder: {source.name}")
    if payload.background:
        background_tasks.add_task(submit_scan, session_id, payload.project_id, source.id)
        return {"session_id": session_id, "status": "queued"}
    _scan_worker(session_id, payload.project_id, source.id)
    return dict(repo.get_session(session_id) or {})


@router.get("/sessions/{session_id}")
def session_status(session_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = _session_for_user(OrganizerRepository(db), db, user, session_id)
    return dict(row)


@router.post("/sessions/{session_id}/retry")
def retry_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    row = _session_for_user(repo, db, user, session_id, "manager")
    if not repo.retry_failed_session(session_id):
        raise HTTPException(409, "Only a failed session with fewer than three attempts can be retried")
    submit_scan(session_id, row["project_id"], row["source_folder_id"])
    return {"session_id": session_id, "status": "queued"}


@router.post("/analyze")
def compatibility_analyze(payload: AnalyzeRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
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
def proposals(project_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    if project_id is None and not user.is_admin:
        raise HTTPException(422, "project_id is required")
    if project_id is not None:
        require_project_role(db, user, project_id, "viewer")
    sql = "SELECT id FROM organizer_proposals"
    params = {}
    if project_id is not None:
        sql += " WHERE project_id=:p"; params["p"] = project_id
    sql += " ORDER BY id DESC LIMIT 100"
    from sqlalchemy import text
    ids = [x[0] for x in db.execute(text(sql), params).all()]
    return {"proposals": [_proposal_payload(repo, i) for i in ids], "count": len(ids)}


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    _proposal_for_user(repo, db, user, proposal_id)
    return _proposal_payload(repo, proposal_id)


@router.patch("/actions/{action_id}")
def edit_action(action_id: int, payload: EditItemRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    row = db.execute(__import__("sqlalchemy").text("SELECT * FROM organizer_actions WHERE id=:id"), {"id": action_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Action not found")
    _proposal_for_user(repo, db, user, row["proposal_id"], "editor")
    valid_folders = {name for name, _ in FOLDER_STRUCTURE}
    if payload.edited_folder and payload.edited_folder not in valid_folders:
        raise HTTPException(422, "Unknown target folder")
    if payload.edited_name is not None:
        edited_name = payload.edited_name.strip()
        if not edited_name or len(edited_name) > 240:
            raise HTTPException(422, "Edited name must contain 1..240 characters")
    repo.edit_item(action_id, payload.decision, payload.edited_name, payload.edited_folder)
    if payload.save_as_rule and payload.edited_folder:
        repo.add_rule({"filename_contains": row["source"]}, {"folder": payload.edited_folder}, None, "user_correction", True)
        db.commit()
    _audit(db, "proposal_action_reviewed", "organizer_action", action_id, f"Decision: {payload.decision}")
    return {"status": "ok", "action_id": action_id}


@router.post("/proposals/{proposal_id}/decision")
def decide(proposal_id: int, payload: DecisionRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    p = _proposal_for_user(repo, db, user, proposal_id, "manager")
    if p["status"] != "waiting_confirmation":
        raise HTTPException(409, "Proposal already processed")
    repo.decide(proposal_id, payload.approved, payload.note)
    _audit(db, "proposal_decided", "organizer_proposal", proposal_id, "Approved" if payload.approved else "Rejected")
    return _proposal_payload(repo, proposal_id)


@router.post("/proposals/{proposal_id}/approve-safe")
def approve_safe(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Approve only deterministic, high-confidence actions; skip every special case."""
    repo = OrganizerRepository(db)
    proposal = _proposal_for_user(repo, db, user, proposal_id, "manager")
    if proposal["status"] != "waiting_confirmation":
        raise HTTPException(409, "Proposal already processed")
    approved = repo.apply_auto_policy(proposal_id, AUTO_APPLY_CONFIDENCE)
    _audit(db, "proposal_safe_actions_approved", "organizer_proposal", proposal_id, f"Approved: {approved}")
    return {"approved": approved, "proposal": _proposal_payload(repo, proposal_id)}


@router.post("/proposals/{proposal_id}/apply")
def apply(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    p = _proposal_for_user(repo, db, user, proposal_id, "manager")
    if not p["copy_folder_id"] or p["copy_folder_id"] == "manual" or p["copy_folder_id"].startswith("virtual:"):
        raise HTTPException(409, "Apply requires a real Google Drive safe copy created by /scan")
    if p["status"] == "conflict_source_changed":
        drive = DriveClient(get_drive_service(project_id=p["project_id"], db=db))
        result = OrganizerExecutor(repo, drive).revalidate_source_conflicts(proposal_id)
        if result["remaining"]:
            raise HTTPException(409, f"После повторной проверки изменены {result['remaining']} файлов; нужен новый снимок")
        p = repo.proposal(proposal_id)
    if p["status"] not in {"approved", "ready_to_apply_to_copy", "applied"}:
        raise HTTPException(409, "Proposal must be approved before apply")
    try:
        drive = DriveClient(get_drive_service(project_id=p["project_id"], db=db))
        if p["status"] == "approved" and not repo.mark_prepared(proposal_id):
            raise HTTPException(409, "Proposal is already being applied or was processed")
        stats = OrganizerExecutor(repo, drive).apply(proposal_id)
        _audit(db, "proposal_applied_to_safe_copy", "organizer_proposal", proposal_id, f"Result: {stats}")
        return {"proposal": _proposal_payload(repo, proposal_id), "stats": stats}
    except HTTPException:
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/proposals/{proposal_id}/rollback")
def rollback(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    p = _proposal_for_user(repo, db, user, proposal_id, "manager")
    try:
        drive = DriveClient(get_drive_service(project_id=p["project_id"], db=db))
        result = OrganizerExecutor(repo, drive).rollback(proposal_id)
        _audit(db, "proposal_rollback", "organizer_proposal", proposal_id, f"Result: {result}")
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/proposals/{proposal_id}/apply-source-one")
def apply_source_one(proposal_id: int, payload: SourceApplyRequest, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if payload.confirmation != "APPLY_ONE_TO_SOURCE":
        raise HTTPException(422, "Exact confirmation phrase is required")
    repo = OrganizerRepository(db)
    proposal = _proposal_for_user(repo, db, user, proposal_id, "owner")
    try:
        drive = DriveClient(get_drive_service(project_id=proposal["project_id"], db=db))
        result = OrganizerExecutor(repo, drive).apply_one_to_source(proposal_id, payload.action_id)
        _audit(db, "proposal_applied_to_source", "organizer_proposal", proposal_id, f"Action: {payload.action_id}; result: {result}")
        return {"proposal": _proposal_payload(repo, proposal_id), "stats": result}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.get("/proposals/{proposal_id}/operations")
def proposal_operations(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    repo = OrganizerRepository(db)
    _proposal_for_user(repo, db, user, proposal_id, "viewer")
    return {"operations": [dict(item) for item in repo.operations(proposal_id)]}


@router.post("/rules")
def create_rule(payload: RuleRequest, db: Session = Depends(get_db), user: User = Depends(require_admin)):
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
