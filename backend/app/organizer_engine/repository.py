from __future__ import annotations

import json
import hashlib
from typing import Any

from sqlalchemy import case, func, text, update
from sqlalchemy.orm import Session

from app.models.organizer import OrganizerSession

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
        allowed = {"copy_folder_id","copy_folder_name","status","progress","error_message","source_item_count","copy_item_count","processed_item_count"}
        fields = {k:v for k,v in fields.items() if k in allowed}
        if not fields:
            return
        self.db.execute(
            update(OrganizerSession)
            .where(OrganizerSession.id == session_id)
            .values(**fields, updated_at=func.now())
        )
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

    def requeue_incomplete_sessions(self, session_ids: list[int]) -> None:
        if not session_ids:
            return
        self.db.execute(
            update(OrganizerSession)
            .where(
                OrganizerSession.id.in_(session_ids),
                OrganizerSession.status.in_(("queued", "scanning", "analyzing")),
            )
            .values(
                status="queued",
                progress=case((OrganizerSession.copy_folder_id.is_(None), 0), else_=55),
                processed_item_count=0,
                updated_at=func.now(),
            )
        )
        self.db.commit()

    def retry_failed_session(self, session_id: int) -> bool:
        result = self.db.execute(text("""
            UPDATE organizer_sessions
            SET status='queued', progress=CASE WHEN copy_folder_id IS NULL THEN 0 ELSE 55 END,
                error_message=NULL, retry_count=retry_count+1, updated_at=now()
            WHERE id=:id AND status='failed' AND retry_count < 2
        """), {"id": session_id})
        self.db.commit()
        return result.rowcount == 1

    def create_proposal(self, project_id: int, session_id: int, folder_name: str, source_folder_id: str, copy_folder_id: str) -> int:
        return int(self.db.execute(text("""
            INSERT INTO organizer_proposals(project_id,folder_name,status,originals_modified,created_at,session_id,source_folder_id,copy_folder_id,idempotency_key)
            VALUES (:project_id,:folder_name,'waiting_confirmation',false,now(),:session_id,:source_folder_id,:copy_folder_id,:idempotency_key)
            ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
            RETURNING id
        """), {**locals(), "idempotency_key": f"organizer-session-{session_id}"}).scalar_one())

    def save_items(self, proposal_id: int, items: list[ProposalItem]) -> None:
        for order, it in enumerate(items, 1):
            self.db.execute(text("""
                INSERT INTO organizer_actions(
                    proposal_id,action_order,action,source,target_folder,proposed_name,requires_confirmation,
                    file_id,current_parent_id,special_case,confidence,reasoning,source_modified_at,source_checksum,user_decision
                ) VALUES (
                    :proposal_id,:action_order,:action,:source,:target_folder,:proposed_name,true,
                    :file_id,:current_parent_id,:special_case,:confidence,:reasoning,:source_modified_at,:source_checksum,'pending'
                )
            """), {
                "proposal_id": proposal_id, "action_order": order, "action": it.kind,
                "source": it.current_name, "target_folder": it.proposed_folder,
                "proposed_name": it.proposed_name, "file_id": it.file_id,
                "current_parent_id": it.current_parent_id, "special_case": it.special_case,
                "confidence": it.confidence, "reasoning": it.reasoning,
                "source_modified_at": it.source_modified_at, "source_checksum": it.source_checksum,
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

    def confirm_selected(self, proposal_id: int) -> int:
        """Approve an explicitly reviewed subset and skip every untouched row."""
        selected = int(self.db.execute(text("""
            SELECT count(*) FROM organizer_actions
            WHERE proposal_id=:id AND user_decision IN ('approved','edited')
        """), {"id": proposal_id}).scalar_one())
        if selected == 0:
            return 0
        self.db.execute(text("""
            UPDATE organizer_actions SET user_decision='skipped'
            WHERE proposal_id=:id AND user_decision='pending'
        """), {"id": proposal_id})
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='approved', note=:note
            WHERE id=:id AND status='waiting_confirmation'
        """), {
            "id": proposal_id,
            "note": f"Пользователь вручную подтвердил выбранные действия: {selected}. Остальные строки пропущены.",
        })
        self.db.commit()
        return selected

    def mark_prepared(self, proposal_id: int) -> bool:
        result = self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='ready_to_apply_to_copy', originals_modified=false, prepared_at=now()
            WHERE id=:id AND status='approved'
        """), {"id": proposal_id})
        self.db.commit()
        return result.rowcount == 1

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

    def mark_applied(self, proposal_id: int):
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='applied', originals_modified=false, applied_at=now()
            WHERE id=:id AND status='ready_to_apply_to_copy'
        """), {"id": proposal_id}); self.db.commit()

    def mark_source_applied(self, proposal_id: int):
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='applied', originals_modified=true, applied_at=now()
            WHERE id=:id
        """), {"id": proposal_id}); self.db.commit()

    def mark_source_conflicts(self, proposal_id: int, action_ids: list[int]) -> None:
        if not action_ids:
            return
        self.db.execute(text("""
            UPDATE organizer_actions
            SET user_decision='conflict_source_changed', special_case='source_changed'
            WHERE proposal_id=:proposal_id AND id = ANY(:action_ids)
        """), {"proposal_id": proposal_id, "action_ids": action_ids})
        self.db.execute(text("""
            UPDATE organizer_proposals SET status='conflict_source_changed',
                note='Источник изменился после анализа. Требуется новый снимок и повторный dry-run.'
            WHERE id=:proposal_id
        """), {"proposal_id": proposal_id})
        self.db.commit()

    def restore_revalidated_conflicts(self, proposal_id: int, action_ids: list[int], remaining: int) -> None:
        if action_ids:
            self.db.execute(text("""
                UPDATE organizer_actions
                SET user_decision='approved', special_case=NULL
                WHERE proposal_id=:proposal_id AND id = ANY(:action_ids)
                  AND user_decision='conflict_source_changed'
            """), {"proposal_id": proposal_id, "action_ids": action_ids})
        if remaining == 0:
            self.db.execute(text("""
                UPDATE organizer_proposals
                SET status='approved', note='Конфликты safe-copy перепроверены; содержимое не изменилось.'
                WHERE id=:proposal_id AND status='conflict_source_changed'
            """), {"proposal_id": proposal_id})
        self.db.commit()

    def skip_remaining_source_conflicts(self, proposal_id: int) -> int:
        result = self.db.execute(text("""
            UPDATE organizer_actions
            SET user_decision='skipped'
            WHERE proposal_id=:proposal_id AND user_decision='conflict_source_changed'
        """), {"proposal_id": proposal_id})
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status='approved',
                note='Изменившиеся файлы safe-copy пропущены; применяются только повторно проверенные действия.'
            WHERE id=:proposal_id AND status='conflict_source_changed'
        """), {"proposal_id": proposal_id})
        self.db.commit()
        return int(result.rowcount)

    def mark_rollback_result(self, proposal_id: int, complete: bool):
        status = "rolled_back" if complete else "rollback_partial"
        self.db.execute(text("""
            UPDATE organizer_proposals
            SET status=:status, rolled_back_at=CASE WHEN :complete THEN now() ELSE rolled_back_at END
            WHERE id=:id
        """), {"id": proposal_id, "status": status, "complete": complete})
        self.db.commit()

    def log_operation(self, proposal_id: int, session_id: int, file_id: str, op_type: str, before: dict, after: dict) -> int:
        raw_key = f"{proposal_id}:{file_id}:{op_type}".encode("utf-8")
        idempotency_key = hashlib.sha256(raw_key).hexdigest()
        return int(self.db.execute(text("""
            INSERT INTO organizer_operations(proposal_id,session_id,file_id,op_type,before_json,after_json,applied_at,idempotency_key)
            VALUES (:proposal_id,:session_id,:file_id,:op_type,CAST(:before AS jsonb),CAST(:after AS jsonb),now(),:idempotency_key)
            ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
            RETURNING id
        """), {"proposal_id":proposal_id,"session_id":session_id,"file_id":file_id,"op_type":op_type,
                 "before":json.dumps(before,ensure_ascii=False),"after":json.dumps(after,ensure_ascii=False),
                 "idempotency_key": idempotency_key}).scalar_one())

    def reconcile_operation(self, proposal_id: int, session_id: int, file_id: str, op_type: str, before: dict, after: dict) -> int:
        """Record the net safe-copy state after an interrupted provider operation."""
        raw_key = f"{proposal_id}:{file_id}:{op_type}".encode("utf-8")
        idempotency_key = hashlib.sha256(raw_key).hexdigest()
        return int(self.db.execute(text("""
            INSERT INTO organizer_operations(proposal_id,session_id,file_id,op_type,before_json,after_json,applied_at,idempotency_key)
            VALUES (:proposal_id,:session_id,:file_id,:op_type,CAST(:before AS jsonb),CAST(:after AS jsonb),now(),:idempotency_key)
            ON CONFLICT (idempotency_key) DO UPDATE
            SET before_json=EXCLUDED.before_json,
                after_json=EXCLUDED.after_json,
                applied_at=EXCLUDED.applied_at,
                rolled_back_at=NULL
            RETURNING id
        """), {"proposal_id":proposal_id,"session_id":session_id,"file_id":file_id,"op_type":op_type,
                 "before":json.dumps(before,ensure_ascii=False),"after":json.dumps(after,ensure_ascii=False),
                 "idempotency_key": idempotency_key}).scalar_one())

    def operations(self, proposal_id: int, limit: int = 5000):
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
