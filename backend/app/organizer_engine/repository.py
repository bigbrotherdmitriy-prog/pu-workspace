from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .types import ProposalItem


class OrganizerRepository:
    def __init__(self, db: Session):
        self.db = db

    def project(self, project_id: int):
        return self.db.execute(text("SELECT id,name FROM projects WHERE id=:id"), {"id": project_id}).mappings().first()

    def create_session(self, project_id: int, source_folder_id: str, source_folder_name: str) -> int:
        return int(self.db.execute(text("""
            INSERT INTO organizer_sessions(project_id,source_folder_id,source_folder_name,status,progress,created_at,updated_at)
            VALUES (:project_id,:source_folder_id,:source_folder_name,'queued',0,now(),now()) RETURNING id
        """), locals()).scalar_one())

    def update_session(self, session_id: int, **fields: Any) -> None:
        allowed = {"copy_folder_id","copy_folder_name","status","progress","error_message","source_item_count","copy_item_count"}
        fields = {k:v for k,v in fields.items() if k in allowed}
        if not fields:
            return
        assigns = ",".join(f"{k}=:{k}" for k in fields) + ",updated_at=now()"
        fields["id"] = session_id
        self.db.execute(text(f"UPDATE organizer_sessions SET {assigns} WHERE id=:id"), fields)
        self.db.commit()

    def get_session(self, session_id: int):
        return self.db.execute(text("SELECT * FROM organizer_sessions WHERE id=:id"), {"id":session_id}).mappings().first()

    def latest_session_for_project(self, project_id: int):
        return self.db.execute(text("SELECT * FROM organizer_sessions WHERE project_id=:p ORDER BY id DESC LIMIT 1"), {"p":project_id}).mappings().first()

    def incomplete_sessions(self):
        return self.db.execute(text("""
            SELECT * FROM organizer_sessions
            WHERE status IN ('queued','scanning','analyzing')
            ORDER BY id
        """)).mappings().all()

    def retry_failed_session(self, session_id: int) -> bool:
        result = self.db.execute(text("""
            UPDATE organizer_sessions
            SET status='queued', progress=CASE WHEN copy_folder_id IS NULL THEN 0 ELSE 55 END,
                error_message=NULL, updated_at=now()
            WHERE id=:id AND status='failed'
        """), {"id": session_id})
        self.db.commit()
        return result.rowcount == 1

    def create_proposal(self, project_id: int, session_id: int, folder_name: str, source_folder_id: str, copy_folder_id: str) -> int:
        return int(self.db.execute(text("""
            INSERT INTO organizer_proposals(project_id,folder_name,status,originals_modified,created_at,session_id,source_folder_id,copy_folder_id)
            VALUES (:project_id,:folder_name,'waiting_confirmation',false,now(),:session_id,:source_folder_id,:copy_folder_id)
            RETURNING id
        """), locals()).scalar_one())

    def save_items(self, proposal_id: int, items: list[ProposalItem]) -> None:
        for order, it in enumerate(items, 1):
            self.db.execute(text("""
                INSERT INTO organizer_actions(
                    proposal_id,action_order,action,source,target_folder,proposed_name,requires_confirmation,
                    file_id,current_parent_id,special_case,confidence,reasoning,user_decision
                ) VALUES (
                    :proposal_id,:action_order,:action,:source,:target_folder,:proposed_name,true,
                    :file_id,:current_parent_id,:special_case,:confidence,:reasoning,'pending'
                )
            """), {
                "proposal_id": proposal_id, "action_order": order, "action": it.kind,
                "source": it.current_name, "target_folder": it.proposed_folder,
                "proposed_name": it.proposed_name, "file_id": it.file_id,
                "current_parent_id": it.current_parent_id, "special_case": it.special_case,
                "confidence": it.confidence, "reasoning": it.reasoning,
            })
        self.db.commit()

    def proposal(self, proposal_id: int):
        return self.db.execute(text("SELECT * FROM organizer_proposals WHERE id=:id"), {"id":proposal_id}).mappings().first()

    def proposal_for_session(self, session_id: int):
        return self.db.execute(text(
            "SELECT * FROM organizer_proposals WHERE session_id=:id"
        ), {"id": session_id}).mappings().first()

    def proposal_items(self, proposal_id: int):
        return self.db.execute(text("SELECT * FROM organizer_actions WHERE proposal_id=:id ORDER BY action_order,id"), {"id":proposal_id}).mappings().all()

    def edit_item(self, action_id: int, decision: str, edited_name: str | None, edited_folder: str | None):
        self.db.execute(text("""
            UPDATE organizer_actions SET user_decision=:decision,edited_name=:edited_name,edited_folder=:edited_folder
            WHERE id=:id
        """), {"id": action_id, "decision": decision, "edited_name": edited_name, "edited_folder": edited_folder}); self.db.commit()

    def decide(self, proposal_id: int, approved: bool, note: str | None):
        status = "approved" if approved else "rejected"
        self.db.execute(text("UPDATE organizer_proposals SET status=:s,note=:n WHERE id=:id"), {"s":status,"n":note,"id":proposal_id})
        if approved:
            self.db.execute(text("UPDATE organizer_actions SET user_decision='approved' WHERE proposal_id=:id AND user_decision='pending'"), {"id":proposal_id})
        self.db.commit()

    def mark_prepared(self, proposal_id: int) -> bool:
        result = self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='ready_to_apply_to_copy', originals_modified=false, prepared_at=now()
            WHERE id=:id AND status='approved'
        """), {"id": proposal_id})
        self.db.commit()

    def apply_auto_policy(self, proposal_id: int, minimum_confidence: float) -> int:
        self.db.execute(text("""
            UPDATE organizer_actions
            SET user_decision = CASE
                WHEN special_case IS NULL AND confidence >= :minimum THEN 'approved'
                ELSE 'skipped'
            END
            WHERE proposal_id=:id
        """), {"id": proposal_id, "minimum": minimum_confidence})
        approved = int(self.db.execute(text("""
            SELECT count(*) FROM organizer_actions
            WHERE proposal_id=:id AND user_decision='approved'
        """), {"id": proposal_id}).scalar_one())
        if approved:
            self.db.execute(text("""
                UPDATE organizer_proposals
                SET status='approved', note=:note
                WHERE id=:id AND status='waiting_confirmation'
            """), {
                "id": proposal_id,
                "note": f"Автоматическая политика: {approved} безопасных действий, порог {minimum_confidence:.0%}.",
            })
        self.db.commit()
        return approved
        return result.rowcount == 1

    def mark_applied(self, proposal_id: int):
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='applied', originals_modified=false, applied_at=now()
            WHERE id=:id AND status='ready_to_apply_to_copy'
        """), {"id": proposal_id}); self.db.commit()

    def mark_rollback_result(self, proposal_id: int, complete: bool):
        status = "rolled_back" if complete else "rollback_partial"
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status=:status, rolled_back_at=CASE WHEN :complete THEN now() ELSE rolled_back_at END
            WHERE id=:id
        """), {"id": proposal_id, "status": status, "complete": complete})
        self.db.commit()

    def log_operation(self, proposal_id: int, session_id: int, file_id: str, op_type: str, before: dict, after: dict) -> int:
        return int(self.db.execute(text("""
            INSERT INTO organizer_operations(proposal_id,session_id,file_id,op_type,before_json,after_json,applied_at)
            VALUES (:proposal_id,:session_id,:file_id,:op_type,CAST(:before AS jsonb),CAST(:after AS jsonb),now()) RETURNING id
        """), {"proposal_id":proposal_id,"session_id":session_id,"file_id":file_id,"op_type":op_type,
                 "before":json.dumps(before,ensure_ascii=False),"after":json.dumps(after,ensure_ascii=False)}).scalar_one())

    def operations(self, proposal_id: int, limit: int = 500):
        return self.db.execute(text("""
            SELECT * FROM organizer_operations WHERE proposal_id=:id ORDER BY id DESC LIMIT :lim
        """), {"id":proposal_id,"lim":limit}).mappings().all()

    def mark_rolled_back(self, op_id: int):
        self.db.execute(text("UPDATE organizer_operations SET rolled_back_at=now() WHERE id=:id"), {"id":op_id}); self.db.commit()

    def confirmed_rules(self):
        rows = self.db.execute(text("SELECT id,pattern_json,action_json,exception_json FROM organizer_rules WHERE confirmed=true ORDER BY id" )).mappings().all()
        return [{"id":r["id"],"pattern":r["pattern_json"],"action":r["action_json"],"exception":r["exception_json"]} for r in rows]

    def add_rule(self, pattern: dict, action: dict, exception: dict | None, source: str, confirmed: bool):
        return int(self.db.execute(text("""
            INSERT INTO organizer_rules(pattern_json,action_json,exception_json,source,confirmed,created_at)
            VALUES (CAST(:p AS jsonb),CAST(:a AS jsonb),CAST(:e AS jsonb),:source,:confirmed,now()) RETURNING id
        """), {"p":json.dumps(pattern,ensure_ascii=False),"a":json.dumps(action,ensure_ascii=False),
                 "e":json.dumps(exception,ensure_ascii=False) if exception else "null","source":source,"confirmed":confirmed}).scalar_one())
