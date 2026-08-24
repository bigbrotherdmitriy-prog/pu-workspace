import os
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/organizer", tags=["organizer"])
DATABASE_URL = os.environ["DATABASE_URL"]


class AnalyzeRequest(BaseModel):
    project_id: int
    folder_name: str = Field(min_length=1, max_length=500)
    files: list[str] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    approved: bool
    note: Optional[str] = None


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def classify_file(name: str) -> str:
    lower = name.lower()
    if lower.endswith((".pdf", ".doc", ".docx", ".txt", ".rtf")):
        return "documents"
    if lower.endswith((".xls", ".xlsx", ".csv")):
        return "tables"
    if lower.endswith((".ppt", ".pptx")):
        return "presentations"
    if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
        return "images"
    if lower.endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
        return "archives"
    return "other"


def proposed_name(name: str) -> str:
    return "_".join(name.strip().split())


def serialize_dt(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def load_proposal(conn, proposal_id: int):
    proposal = conn.execute("""
        SELECT id, project_id, folder_name, status, note,
               originals_modified, created_at, prepared_at
        FROM organizer_proposals
        WHERE id=%s
    """, (proposal_id,)).fetchone()

    if proposal is None:
        return None

    actions = conn.execute("""
        SELECT action_order AS "order", action, source,
               target_folder, proposed_name, requires_confirmation
        FROM organizer_actions
        WHERE proposal_id=%s
        ORDER BY action_order, id
    """, (proposal_id,)).fetchall()

    result = dict(proposal)
    result["created_at"] = serialize_dt(result["created_at"])
    result["prepared_at"] = serialize_dt(result["prepared_at"])
    result["actions"] = [dict(x) for x in actions]
    return result


@router.get("/status")
def organizer_status():
    with get_conn() as conn:
        conn.execute("SELECT 1")
    return {
        "status": "ok",
        "module": "organizer",
        "mode": "postgres-preview-first",
        "storage": "postgresql",
        "originals_modified": False,
    }


@router.post("/analyze")
def analyze_folder(payload: AnalyzeRequest):
    actions = []

    for index, filename in enumerate(payload.files, start=1):
        actions.append({
            "order": index,
            "action": "organize",
            "source": filename,
            "target_folder": classify_file(filename),
            "proposed_name": proposed_name(filename),
            "requires_confirmation": True,
        })

    with get_conn() as conn:
        project = conn.execute(
            "SELECT id FROM projects WHERE id=%s",
            (payload.project_id,)
        ).fetchone()

        if project is None:
            raise HTTPException(404, "Project not found")

        row = conn.execute("""
            INSERT INTO organizer_proposals
                (project_id, folder_name, status,
                 originals_modified, created_at)
            VALUES (%s,%s,'waiting_confirmation',FALSE,%s)
            RETURNING id
        """, (
            payload.project_id,
            payload.folder_name,
            datetime.now(timezone.utc)
        )).fetchone()

        proposal_id = row["id"]

        for action in actions:
            conn.execute("""
                INSERT INTO organizer_actions
                    (proposal_id, action_order, action, source,
                     target_folder, proposed_name,
                     requires_confirmation)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (
                proposal_id,
                action["order"],
                action["action"],
                action["source"],
                action["target_folder"],
                action["proposed_name"],
                action["requires_confirmation"],
            ))

        conn.commit()
        return load_proposal(conn, proposal_id)


@router.get("/proposals")
def list_proposals():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM organizer_proposals ORDER BY id"
        ).fetchall()

        proposals = [load_proposal(conn, x["id"]) for x in rows]

        return {
            "proposals": proposals,
            "count": len(proposals)
        }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int):
    with get_conn() as conn:
        proposal = load_proposal(conn, proposal_id)
        if proposal is None:
            raise HTTPException(404, "Proposal not found")
        return proposal


@router.post("/proposals/{proposal_id}/decision")
def decide_proposal(proposal_id: int, payload: DecisionRequest):
    with get_conn() as conn:
        proposal = load_proposal(conn, proposal_id)

        if proposal is None:
            raise HTTPException(404, "Proposal not found")

        status = "approved" if payload.approved else "rejected"

        conn.execute("""
            UPDATE organizer_proposals
            SET status=%s, note=%s
            WHERE id=%s
        """, (status, payload.note, proposal_id))

        conn.commit()
        return load_proposal(conn, proposal_id)


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int):
    with get_conn() as conn:
        proposal = load_proposal(conn, proposal_id)

        if proposal is None:
            raise HTTPException(404, "Proposal not found")

        if proposal["status"] != "approved":
            raise HTTPException(
                409,
                "Proposal must be approved before apply"
            )

        conn.execute("""
            UPDATE organizer_proposals
            SET status='ready_to_apply_to_copy',
                originals_modified=FALSE,
                prepared_at=%s
            WHERE id=%s
        """, (datetime.now(timezone.utc), proposal_id))

        conn.commit()
        return load_proposal(conn, proposal_id)
