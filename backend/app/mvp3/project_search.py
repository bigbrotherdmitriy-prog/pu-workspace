from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models.ai_secretary import Message
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task


ALLOWED_TYPES = frozenset({
    "project", "document", "contract", "task", "obligation", "risk", "decision", "message",
})
MAX_QUERY = 200
MAX_LIMIT = 100
MAX_SCAN_ROWS_PER_TYPE = 500


class SearchDenied(RuntimeError):
    pass


class SearchUnavailable(RuntimeError):
    pass


class SearchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SearchFilters:
    query: str | None = None
    types: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    contract_id: int | None = None
    counterparty: str | None = None

    def __post_init__(self) -> None:
        query = self.query.strip() if isinstance(self.query, str) else self.query
        counterparty = self.counterparty.strip() if isinstance(self.counterparty, str) else self.counterparty
        object.__setattr__(self, "query", query or None)
        object.__setattr__(self, "counterparty", counterparty or None)
        if query is not None and (not isinstance(query, str) or len(query) > MAX_QUERY):
            raise SearchValidationError("invalid_query")
        if counterparty is not None and (
            not isinstance(counterparty, str) or len(counterparty) > MAX_QUERY
        ):
            raise SearchValidationError("invalid_counterparty")
        if not isinstance(self.types, tuple):
            object.__setattr__(self, "types", tuple(self.types))
        if any(kind not in ALLOWED_TYPES for kind in self.types):
            raise SearchValidationError("invalid_type")
        if len(set(self.types)) != len(self.types):
            raise SearchValidationError("duplicate_type")
        if self.contract_id is not None and (
            type(self.contract_id) is not int or self.contract_id < 1
        ):
            raise SearchValidationError("invalid_contract_id")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise SearchValidationError("invalid_date_range")

    def mapping(self) -> dict:
        return {
            "query": self.query,
            "types": sorted(self.types),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "contract_id": self.contract_id,
            "counterparty": self.counterparty,
        }


def _cursor_secret() -> bytes:
    value = os.getenv("APP_SECRET_KEY", "")
    if len(value) < 32:
        raise SearchUnavailable("search_cursor_secret_not_configured")
    return value.encode("utf-8")


def _pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains(columns: Iterable, value: str):
    pattern = _pattern(value)
    return or_(*(column.ilike(pattern, escape="\\") for column in columns))


def _as_utc(value: date | datetime | None) -> datetime:
    if value is None:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, timezone.utc)


def _fingerprint(*, organization_id: int, project_id: int, actor_user_id: int,
                 filters: SearchFilters) -> str:
    raw = json.dumps({
        "organization_id": organization_id,
        "project_id": project_id,
        "actor_user_id": actor_user_id,
        "filters": filters.mapping(),
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _encode_cursor(item: dict, fingerprint: str) -> str:
    payload = json.dumps({
        "v": 1,
        "at": item["_sort_at"].isoformat(),
        "type": item["entity_type"],
        "id": item["entity_id"],
        "fp": fingerprint,
    }, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(_cursor_secret(), encoded, hashlib.sha256).digest()
    return (encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def _decode_cursor(value: str, fingerprint: str) -> tuple[datetime, str, int]:
    try:
        if not value or len(value) > 768:
            raise ValueError
        encoded, supplied = value.encode("ascii").split(b".", 1)
        signature = base64.urlsafe_b64decode(supplied + b"=" * (-len(supplied) % 4))
        expected = hmac.new(_cursor_secret(), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        raw = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        data = json.loads(raw)
        if set(data) != {"v", "at", "type", "id", "fp"} or data["v"] != 1:
            raise ValueError
        if data["fp"] != fingerprint or data["type"] not in ALLOWED_TYPES:
            raise ValueError
        if type(data["id"]) is not int:
            raise ValueError
        return _as_utc(datetime.fromisoformat(data["at"])), data["type"], data["id"]
    except SearchUnavailable:
        raise
    except Exception as exc:
        raise SearchValidationError("invalid_cursor") from exc


def _sort_key(item: dict) -> tuple[float, str, int]:
    return (-item["_sort_at"].timestamp(), item["entity_type"], item["entity_id"])


def _date_allowed(value: date | datetime | None, filters: SearchFilters) -> bool:
    if value is None:
        return filters.date_from is None and filters.date_to is None
    current = _as_utc(value).date()
    return not (filters.date_from and current < filters.date_from) and not (
        filters.date_to and current > filters.date_to
    )


def _authorize(db: Session, *, project_id: int, actor_user_id: int) -> Project:
    # Project search deliberately requires an explicit membership even for an
    # administrator; global admin status is not a cross-tenant search grant.
    row = db.scalar(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id == project_id, ProjectMember.user_id == actor_user_id)
    )
    if row is None:
        raise SearchDenied("scope_unavailable")
    return row


def _navigation(kind: str, project_id: int, entity_id: int) -> dict:
    section = {
        "project": "Проект",
        "document": "Документы",
        "contract": "Договоры",
        "task": "Задачи",
        "obligation": "Обязательства",
        "risk": "Риски и решения",
        "decision": "Риски и решения",
        "message": "Письма",
    }[kind]
    return {
        "section": section,
        "project_id": project_id,
        "entity_type": kind,
        "entity_id": entity_id,
    }


def _result(*, kind: str, entity_id: int, name: str, at: date | datetime | None,
            project_id: int, status: str | None, contract: Contract | None = None,
            counterparty: str | None = None) -> dict:
    return {
        "entity_type": kind,
        "entity_id": entity_id,
        "name": name,
        "date": at.isoformat() if at else None,
        "project_id": project_id,
        "contract_id": contract.id if contract else None,
        "counterparty": counterparty if counterparty is not None else (
            contract.counterparty if contract else None
        ),
        "status": status,
        "navigation": _navigation(kind, project_id, entity_id),
        "_sort_at": _as_utc(at),
    }


def _linked_contract(db: Session, project_id: int, *, document_id: int | None = None,
                     task: Task | None = None, obligation_id: int | None = None) -> Contract | None:
    if document_id is not None:
        return db.scalar(select(Contract).where(
            Contract.project_id == project_id,
            or_(
                Contract.source_document_id == document_id,
                exists(select(ContractDocumentLink.id).where(
                    ContractDocumentLink.project_id == project_id,
                    ContractDocumentLink.contract_id == Contract.id,
                    ContractDocumentLink.document_id == document_id,
                )),
            ),
        ).order_by(Contract.id).limit(1))
    obligation = None
    if obligation_id is not None:
        obligation = db.scalar(select(Obligation).where(
            Obligation.id == obligation_id,
            Obligation.project_id == project_id,
        ))
    elif task is not None:
        obligation = db.scalar(select(Obligation).where(
            Obligation.task_id == task.id,
            Obligation.project_id == project_id,
        ).order_by(Obligation.id).limit(1))
        if obligation is None and task.message_id is not None:
            contract_id = db.scalar(select(Message.contract_id).where(
                Message.id == task.message_id,
                Message.project_id == project_id,
                Message.context_confirmed.is_(True),
            ))
            if contract_id is not None:
                return db.scalar(select(Contract).where(
                    Contract.id == contract_id,
                    Contract.project_id == project_id,
                ))
    if obligation is None or obligation.contract_id is None:
        return None
    return db.scalar(select(Contract).where(
        Contract.id == obligation.contract_id,
        Contract.project_id == project_id,
    ))


def _contract_allowed(contract: Contract | None, filters: SearchFilters) -> bool:
    if filters.contract_id is not None and (
        contract is None or contract.id != filters.contract_id
    ):
        return False
    if filters.counterparty is not None and (
        contract is None
        or filters.counterparty.casefold() not in (contract.counterparty or "").casefold()
    ):
        return False
    return True


def _bounded_rows(db: Session, query) -> tuple[list, bool]:
    rows = list(db.scalars(query.limit(MAX_SCAN_ROWS_PER_TYPE + 1)))
    return rows[:MAX_SCAN_ROWS_PER_TYPE], len(rows) > MAX_SCAN_ROWS_PER_TYPE


def _collect(db: Session, project: Project, filters: SearchFilters) -> tuple[list[dict], bool]:
    kinds = set(filters.types or ALLOWED_TYPES)
    items: list[dict] = []
    truncated = False

    if "project" in kinds and not any((
        filters.contract_id, filters.counterparty, filters.date_from, filters.date_to,
    )) and (not filters.query or filters.query.casefold() in project.name.casefold()):
        items.append(_result(
            kind="project", entity_id=project.id, name=project.name, at=None,
            project_id=project.id, status="archived" if project.archived_at else "active",
        ))

    if "contract" in kinds:
        query = select(Contract).where(Contract.project_id == project.id)
        if filters.query:
            query = query.where(_contains(
                (Contract.number, Contract.title, Contract.counterparty), filters.query,
            ))
        if filters.contract_id:
            query = query.where(Contract.id == filters.contract_id)
        rows, cut = _bounded_rows(db, query.order_by(Contract.id))
        truncated = truncated or cut
        for row in rows:
            if _contract_allowed(row, filters) and _date_allowed(row.signed_at, filters):
                items.append(_result(
                    kind="contract", entity_id=row.id, name=f"{row.number} — {row.title}",
                    at=row.signed_at, project_id=project.id, status=row.status,
                    contract=row, counterparty=row.counterparty,
                ))

    if "document" in kinds:
        query = select(Document).where(Document.project_id == project.id)
        if filters.query:
            query = query.where(_contains((Document.name,), filters.query))
        rows, cut = _bounded_rows(db, query.order_by(Document.id))
        truncated = truncated or cut
        for row in rows:
            contract = _linked_contract(db, project.id, document_id=row.id)
            if _contract_allowed(contract, filters) and _date_allowed(row.source_modified_at, filters):
                items.append(_result(
                    kind="document", entity_id=row.id, name=row.name,
                    at=row.source_modified_at, project_id=project.id, status=row.status,
                    contract=contract,
                ))

    if "task" in kinds:
        query = select(Task).where(Task.project_id == project.id)
        if filters.query:
            query = query.where(_contains((Task.title,), filters.query))
        rows, cut = _bounded_rows(db, query.order_by(Task.id))
        truncated = truncated or cut
        for row in rows:
            contract = _linked_contract(db, project.id, task=row)
            if _contract_allowed(contract, filters) and _date_allowed(row.due_date, filters):
                items.append(_result(
                    kind="task", entity_id=row.id, name=row.title, at=row.due_date,
                    project_id=project.id, status=row.status, contract=contract,
                ))

    if "message" in kinds:
        query = select(Message).where(
            Message.project_id == project.id,
            Message.organization_id == project.organization_id,
            Message.context_confirmed.is_(True),
        )
        if filters.query:
            query = query.where(_contains((Message.source_name,), filters.query))
        rows, cut = _bounded_rows(db, query.order_by(Message.id))
        truncated = truncated or cut
        for row in rows:
            contract = db.scalar(select(Contract).where(
                Contract.id == row.contract_id,
                Contract.project_id == project.id,
            )) if row.contract_id else None
            if _contract_allowed(contract, filters) and _date_allowed(row.updated_at, filters):
                items.append(_result(
                    kind="message", entity_id=row.id, name=row.source_name, at=row.updated_at,
                    project_id=project.id, status=row.status, contract=contract,
                ))

    for kind, model, name_column, date_column in (
        ("obligation", Obligation, Obligation.title, Obligation.due_date),
        ("risk", Risk, Risk.title, Risk.updated_at),
        ("decision", Decision, Decision.question, Decision.updated_at),
    ):
        if kind not in kinds:
            continue
        query = select(model).where(model.project_id == project.id)
        if filters.query:
            query = query.where(_contains((name_column,), filters.query))
        rows, cut = _bounded_rows(db, query.order_by(model.id))
        truncated = truncated or cut
        for row in rows:
            contract = _linked_contract(
                db,
                project.id,
                obligation_id=row.id if kind == "obligation" else row.obligation_id,
            )
            at = getattr(row, date_column.key)
            if _contract_allowed(contract, filters) and _date_allowed(at, filters):
                items.append(_result(
                    kind=kind,
                    entity_id=row.id,
                    name=getattr(row, name_column.key),
                    at=at,
                    project_id=project.id,
                    status=row.status,
                    contract=contract,
                ))
    return items, truncated


def project_search(db: Session, *, project_id: int, actor_user_id: int,
                   filters: SearchFilters, limit: int = 50,
                   cursor: str | None = None) -> dict:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise SearchValidationError("invalid_limit")
    project = _authorize(db, project_id=project_id, actor_user_id=actor_user_id)
    fingerprint = _fingerprint(
        organization_id=project.organization_id,
        project_id=project.id,
        actor_user_id=actor_user_id,
        filters=filters,
    )
    decoded = _decode_cursor(cursor, fingerprint) if cursor else None
    collected, scan_truncated = _collect(db, project, filters)
    collected.sort(key=_sort_key)
    if decoded is not None:
        cursor_key = (-decoded[0].timestamp(), decoded[1], decoded[2])
        collected = [item for item in collected if _sort_key(item) > cursor_key]
    page = collected[:limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = _encode_cursor(page[-1], fingerprint) if has_more and page else None
    for item in page:
        item.pop("_sort_at")
    return {
        "items": page,
        "next_cursor": next_cursor,
        "limit": limit,
        "scan_truncated": scan_truncated,
        "scan_cap_per_type": MAX_SCAN_ROWS_PER_TYPE,
        "external_actions_created": False,
    }
