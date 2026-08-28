from __future__ import annotations
import hashlib
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.governance import Decision, Risk
from app.models.project_member import ProjectMember
from app.models.user import User
from app.core.integration_types import StorageObject

SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
RISK_RE = re.compile(r"\b(риск\w*|угроз\w*|вероятн\w*|может привести|возможн\w* задерж\w*|просроч\w*|дефицит\w*|нехватк\w*|отклонен\w*)", re.I)
DECISION_RE = re.compile(r"\b(требуется решение|необходимо решить|нужно решить|на согласование|следует выбрать|утвердить|согласовать вариант)\b", re.I)
HIGH_RE = re.compile(r"\b(критич\w*|существен\w*|срыв\w*|авар\w*|штраф\w*|просроч\w*)", re.I)


def extract_governance_candidates(text: str) -> tuple[list[dict], list[dict]]:
    risks: list[dict] = []
    decisions: list[dict] = []
    for raw in SPLIT_RE.split(text or ""):
        sentence = " ".join(raw.split()).strip(" -–—\t")
        if not 20 <= len(sentence) <= 1200:
            continue
        if RISK_RE.search(sentence):
            risks.append({
                "text": sentence,
                "kind": "deviation" if re.search(r"\b(просроч\w*|отклонен\w*)", sentence, re.I) else "risk",
                "criticality": "high" if HIGH_RE.search(sentence) else "medium",
            })
        if DECISION_RE.search(sentence):
            decisions.append({"text": sentence})
    return risks, decisions


def _owner(db: Session, project_id: int) -> User | None:
    return db.scalar(select(User).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id).order_by((ProjectMember.role == "owner").desc(), User.id))


def _source_digest(kind: str, sentence: str) -> str:
    return hashlib.sha256((kind + ":" + sentence.casefold()).encode()).hexdigest()


def create_governance_items(db: Session, project_id: int, files: list[StorageObject], source_type: str = "document_analysis") -> tuple[list[Risk], list[Decision]]:
    owner = _owner(db, project_id)
    if not owner:
        return [], []
    risks: list[Risk] = []; decisions: list[Decision] = []
    # SessionLocal has autoflush disabled. Track hashes added in this batch so
    # identical clauses from multiple files cannot violate unique constraints.
    known_risk_hashes = set(db.scalars(select(Risk.source_hash).where(Risk.project_id == project_id)).all())
    known_decision_hashes = set(db.scalars(select(Decision.source_hash).where(Decision.project_id == project_id)).all())
    for file in files:
        if file.is_folder or not file.content_text:
            continue
        risk_candidates, decision_candidates = extract_governance_candidates(file.content_text)
        for candidate in risk_candidates:
            sentence = candidate["text"]
            digest = _source_digest("risk", sentence)
            if digest not in known_risk_hashes:
                item = Risk(project_id=project_id, owner_user_id=owner.id, kind=candidate["kind"], title=sentence[:240], description=sentence, criticality=candidate["criticality"], source_type=source_type, source_id=file.id, source_name=file.name, source_excerpt=sentence, source_hash=digest, confidence=0.82)
                db.add(item); risks.append(item); known_risk_hashes.add(digest)
        for candidate in decision_candidates:
            sentence = candidate["text"]
            digest = _source_digest("decision", sentence)
            if digest not in known_decision_hashes:
                item = Decision(project_id=project_id, initiator_user_id=owner.id, question=sentence, source_type=source_type, source_id=file.id, source_name=file.name, source_excerpt=sentence, source_hash=digest, confidence=0.80)
                db.add(item); decisions.append(item); known_decision_hashes.add(digest)
    db.commit()
    return risks, decisions
