from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.ai_secretary import Message
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.governance import Decision, Risk
from app.models.management import Obligation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.search import SavedSearchView, SavedSearchViewHistory
from app.models.task import Task
from app.models.v54_pilot import Evidence, SourceReference


ALLOWED_TYPES = frozenset({"project", "document", "contract", "task", "obligation", "risk", "decision", "message"})
FILTER_KEYS = frozenset({"query", "types", "date_from", "date_to", "contract_id", "counterparty"})
MAX_QUERY = 200
MAX_LIMIT = 100
MAX_SCAN_ROWS_PER_TYPE = 1000


class SearchDenied(RuntimeError):
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

    def __post_init__(self):
        query = self.query.strip() if isinstance(self.query, str) else self.query
        counterparty = self.counterparty.strip() if isinstance(self.counterparty, str) else self.counterparty
        object.__setattr__(self, "query", query or None)
        object.__setattr__(self, "counterparty", counterparty or None)
        if query is not None and (not isinstance(query, str) or len(query) > MAX_QUERY):
            raise SearchValidationError("invalid_query")
        if counterparty is not None and (not isinstance(counterparty, str) or len(counterparty) > MAX_QUERY):
            raise SearchValidationError("invalid_counterparty")
        if not isinstance(self.types, tuple):
            object.__setattr__(self, "types", tuple(self.types))
        if any(kind not in ALLOWED_TYPES for kind in self.types):
            raise SearchValidationError("invalid_type")
        if len(set(self.types)) != len(self.types):
            raise SearchValidationError("duplicate_type")
        if self.contract_id is not None and (type(self.contract_id) is not int or self.contract_id < 1):
            raise SearchValidationError("invalid_contract_id")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise SearchValidationError("invalid_date_range")

    def mapping(self) -> dict:
        return {
            "query": self.query,
            "types": list(self.types),
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "contract_id": self.contract_id,
            "counterparty": self.counterparty,
        }

    @classmethod
    def from_mapping(cls, value: dict) -> "SearchFilters":
        if not isinstance(value, dict) or set(value) - FILTER_KEYS:
            raise SearchValidationError("invalid_filter_keys")
        query = value.get("query")
        counterparty = value.get("counterparty")
        types = value.get("types", ())
        if query is not None and not isinstance(query, str):
            raise SearchValidationError("invalid_query")
        if counterparty is not None and not isinstance(counterparty, str):
            raise SearchValidationError("invalid_counterparty")
        if not isinstance(types, (list, tuple)) or any(not isinstance(item, str) for item in types):
            raise SearchValidationError("invalid_type")
        try:
            start = date.fromisoformat(value["date_from"]) if value.get("date_from") else None
            end = date.fromisoformat(value["date_to"]) if value.get("date_to") else None
        except (TypeError, ValueError) as exc:
            raise SearchValidationError("invalid_date_range") from exc
        return cls(
            query=query,
            types=tuple(types),
            date_from=start,
            date_to=end,
            contract_id=value.get("contract_id"),
            counterparty=counterparty,
        )


def _project(db: Session, organization_id: int, project_id: int) -> Project:
    row = db.scalar(select(Project).where(Project.id == project_id, Project.organization_id == organization_id))
    if row is None:
        raise SearchDenied("scope_unavailable")
    return row


def _can_view(db: Session, actor_user_id: int, project_id: int) -> bool:
    return db.scalar(select(ProjectMember.id).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == actor_user_id,
    )) is not None


def _authorize(db: Session, organization_id: int, project_id: int, actor_user_id: int) -> Project:
    project = _project(db, organization_id, project_id)
    if not _can_view(db, actor_user_id, project_id):
        raise SearchDenied("scope_unavailable")
    return project


def _pattern(value: str) -> str:
    # Parameters stay bound; escaping makes wildcard characters literal as well.
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


def _cursor_fingerprint(*, organization_id: int, project_id: int, actor_user_id: int, filters: SearchFilters) -> str:
    payload = {
        "organization_id": organization_id,
        "project_id": project_id,
        "actor_user_id": actor_user_id,
        "filters": filters.mapping(),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _encode_cursor(item: dict, fingerprint: str) -> str:
    raw = json.dumps({
        "v": 1,
        "at": item["sort_at"].isoformat(),
        "type": item["entity_type"],
        "id": item["entity_id"],
        "fp": fingerprint,
    }, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(value: str, fingerprint: str) -> tuple[datetime, str, int]:
    try:
        if not value or len(value) > 512:
            raise ValueError
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        data = json.loads(raw)
        at = datetime.fromisoformat(data["at"])
        if data != {"v": 1, "at": data["at"], "type": data["type"], "id": data["id"], "fp": data["fp"]}:
            raise ValueError
        if data["fp"] != fingerprint or data["type"] not in ALLOWED_TYPES or type(data["id"]) is not int:
            raise ValueError
        return _as_utc(at), data["type"], data["id"]
    except Exception as exc:
        raise SearchValidationError("invalid_cursor") from exc


def _after_cursor(item: dict, cursor: tuple[datetime, str, int] | None) -> bool:
    if cursor is None:
        return True
    key = (-item["sort_at"].timestamp(), item["entity_type"], item["entity_id"])
    cursor_key = (-cursor[0].timestamp(), cursor[1], cursor[2])
    return key > cursor_key


def _date_allowed(value: date | datetime | None, filters: SearchFilters) -> bool:
    current = _as_utc(value).date()
    if value is None and (filters.date_from or filters.date_to):
        return False
    return not (filters.date_from and current < filters.date_from) and not (
        filters.date_to and current > filters.date_to
    )


def _evidence_links(db: Session, *, organization_id: int, project_id: int, pins: list | None) -> list[dict]:
    ids = []
    for pin in pins or []:
        try:
            if (pin.get("version_kind") == "revision" and pin.get("value") == 1
                    and pin["ref"]["type"] == "evidence"):
                ids.append(pin["ref"]["id"]["value"])
        except (AttributeError, KeyError, TypeError):
            continue
    if not ids:
        return []
    allowed = set(db.scalars(
        select(Evidence.id).join(SourceReference, and_(
            SourceReference.id == Evidence.source_id,
            SourceReference.organization_id == Evidence.organization_id,
        )).where(
            Evidence.organization_id == organization_id,
            Evidence.id.in_(ids),
            SourceReference.origin_project_id == project_id,
        )
    ))
    return [{
        "relation": "evidence",
        "href": f"/api/v54/evidence/{evidence_id}/fragment?revision=1",
        "revision": 1,
    } for evidence_id in ids if evidence_id in allowed]


def _source_evidence_links(db: Session, *, organization_id: int, project_id: int,
                           source_reference_id: str | None) -> list[dict]:
    if not source_reference_id:
        return []
    ids = db.scalars(select(Evidence.id).join(SourceReference, and_(
        SourceReference.id == Evidence.source_id,
        SourceReference.organization_id == Evidence.organization_id,
    )).where(
        Evidence.organization_id == organization_id,
        Evidence.source_id == source_reference_id,
        SourceReference.origin_project_id == project_id,
    ).order_by(Evidence.id).limit(20)).all()
    return [{
        "relation": "evidence",
        "href": f"/api/v54/evidence/{evidence_id}/fragment?revision=1",
        "revision": 1,
    } for evidence_id in ids]


def _result(*, kind: str, entity_id: int, name: str, at, project: Project, status: str | None,
            contract: Contract | None = None, links: list[dict] | None = None, counterparty: str | None = None) -> dict:
    return {
        "entity_type": kind,
        "entity_id": entity_id,
        "name": name,
        "date": at.isoformat() if at else None,
        "sort_at": _as_utc(at),
        "project": {"id": project.id, "name": project.name},
        "contract": ({"id": contract.id, "number": contract.number, "title": contract.title} if contract else None),
        "counterparty": counterparty if counterparty is not None else (contract.counterparty if contract else None),
        "status": status,
        "links": links or [],
    }


def _search_contracts(db, project, filters):
    query = select(Contract).where(Contract.project_id == project.id)
    if filters.query:
        query = query.where(_contains((Contract.number, Contract.title, Contract.counterparty), filters.query))
    if filters.contract_id:
        query = query.where(Contract.id == filters.contract_id)
    rows = db.scalars(query.order_by(Contract.id).limit(MAX_SCAN_ROWS_PER_TYPE + 1)).all()
    truncated = len(rows) > MAX_SCAN_ROWS_PER_TYPE
    rows = rows[:MAX_SCAN_ROWS_PER_TYPE]
    return ([_result(kind="contract", entity_id=row.id, name=f"{row.number} — {row.title}", at=row.signed_at,
                    project=project, status=row.status, contract=row, counterparty=row.counterparty,
                    links=[{"relation": "entity", "href": f"/projects/{project.id}/contracts/{row.id}"}])
            for row in rows if _date_allowed(row.signed_at, filters)
            and (not filters.counterparty
                 or filters.counterparty.casefold() in (row.counterparty or "").casefold())], truncated)


def _linked_contract(db, project_id: int, *, document_id=None, task_id=None, obligation_id=None):
    if document_id is not None:
        row = db.scalar(select(Contract).where(
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
        return row
    obligation = None
    if obligation_id is not None:
        obligation = db.scalar(select(Obligation).where(
            Obligation.id == obligation_id, Obligation.project_id == project_id,
        ))
    elif task_id is not None:
        obligation = db.scalar(select(Obligation).where(
            Obligation.task_id == task_id, Obligation.project_id == project_id,
        ).order_by(Obligation.id).limit(1))
    return db.get(Contract, obligation.contract_id) if obligation and obligation.contract_id else None


def _contract_filter_match(contract, filters):
    if filters.contract_id and (contract is None or contract.id != filters.contract_id):
        return False
    if filters.counterparty and (contract is None or filters.counterparty.casefold() not in (contract.counterparty or "").casefold()):
        return False
    return True


def _collect(db: Session, project: Project, organization_id: int, filters: SearchFilters) -> tuple[list[dict], bool]:
    kinds = set(filters.types or ALLOWED_TYPES)
    items: list[dict] = []
    truncated = False
    if "project" in kinds and not filters.contract_id and not filters.counterparty and not filters.date_from and not filters.date_to:
        if not filters.query or filters.query.casefold() in project.name.casefold():
            items.append(_result(kind="project", entity_id=project.id, name=project.name, at=None, project=project,
                                 status="archived" if project.archived_at else "active",
                                 links=[{"relation": "entity", "href": f"/projects/{project.id}"}]))
    if "contract" in kinds:
        contract_items, contract_truncated = _search_contracts(db, project, filters)
        items.extend(contract_items)
        truncated = truncated or contract_truncated

    if "document" in kinds:
        query = select(Document).where(Document.project_id == project.id)
        if filters.query:
            query = query.where(_contains((Document.name, Document.mime_type), filters.query))
        rows = db.scalars(query.order_by(Document.id).limit(MAX_SCAN_ROWS_PER_TYPE + 1)).all()
        truncated = truncated or len(rows) > MAX_SCAN_ROWS_PER_TYPE
        for row in rows[:MAX_SCAN_ROWS_PER_TYPE]:
            contract = _linked_contract(db, project.id, document_id=row.id)
            if not _contract_filter_match(contract, filters) or not _date_allowed(row.source_modified_at, filters):
                continue
            links = [{"relation": "entity", "href": f"/projects/{project.id}/documents/{row.id}"}]
            if contract:
                links.append({"relation": "contract", "href": f"/projects/{project.id}/contracts/{contract.id}"})
            items.append(_result(kind="document", entity_id=row.id, name=row.name, at=row.source_modified_at,
                                 project=project, status=row.status, contract=contract, links=links))

    if "task" in kinds:
        query = select(Task).where(Task.project_id == project.id)
        if filters.query:
            query = query.where(_contains((Task.title, Task.source_file_name), filters.query))
        rows = db.scalars(query.order_by(Task.id).limit(MAX_SCAN_ROWS_PER_TYPE + 1)).all()
        truncated = truncated or len(rows) > MAX_SCAN_ROWS_PER_TYPE
        for row in rows[:MAX_SCAN_ROWS_PER_TYPE]:
            contract = _linked_contract(db, project.id, task_id=row.id)
            if not _contract_filter_match(contract, filters) or not _date_allowed(row.due_date, filters):
                continue
            items.append(_result(kind="task", entity_id=row.id, name=row.title, at=row.due_date, project=project,
                                 status=row.status, contract=contract,
                                 links=[{"relation": "entity", "href": f"/tasks/{row.id}"}]))

    if "message" in kinds:
        query = select(Message).where(
            Message.project_id == project.id,
            Message.organization_id == organization_id,
        )
        if filters.query:
            query = query.where(_contains((Message.source_name,), filters.query))
        rows = db.scalars(query.order_by(Message.id).limit(MAX_SCAN_ROWS_PER_TYPE + 1)).all()
        truncated = truncated or len(rows) > MAX_SCAN_ROWS_PER_TYPE
        for row in rows[:MAX_SCAN_ROWS_PER_TYPE]:
            contract = db.scalar(select(Contract).where(
                Contract.id == row.contract_id,
                Contract.project_id == project.id,
            )) if row.contract_id else None
            if not _contract_filter_match(contract, filters) or not _date_allowed(row.updated_at, filters):
                continue
            links = [{"relation": "entity", "href": f"/ai-secretary/inbox?project_id={project.id}"}]
            links.extend(_source_evidence_links(
                db,
                organization_id=organization_id,
                project_id=project.id,
                source_reference_id=row.source_reference_id,
            ))
            items.append(_result(
                kind="message",
                entity_id=row.id,
                name=row.source_name,
                at=row.updated_at,
                project=project,
                status=row.status,
                contract=contract,
                links=links,
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
            query = query.where(_contains((name_column, model.source_name), filters.query))
        rows = db.scalars(query.order_by(model.id).limit(MAX_SCAN_ROWS_PER_TYPE + 1)).all()
        truncated = truncated or len(rows) > MAX_SCAN_ROWS_PER_TYPE
        for row in rows[:MAX_SCAN_ROWS_PER_TYPE]:
            contract = _linked_contract(
                db, project.id,
                obligation_id=row.id if kind == "obligation" else row.obligation_id,
            )
            at = getattr(row, date_column.key)
            if not _contract_filter_match(contract, filters) or not _date_allowed(at, filters):
                continue
            links = [{"relation": "entity", "href": f"/management/v2/{kind}s/{row.id}"}]
            links.extend(_evidence_links(
                db,
                organization_id=organization_id,
                project_id=project.id,
                pins=row.evidence_pins,
            ))
            items.append(_result(kind=kind, entity_id=row.id, name=getattr(row, name_column.key), at=at,
                                 project=project, status=row.status, contract=contract, links=links))
    return items, truncated


def project_search(db: Session, *, organization_id: int, project_id: int, actor_user_id: int,
                   filters: SearchFilters, limit: int = 50, cursor: str | None = None) -> dict:
    if type(limit) is not int or limit < 1 or limit > MAX_LIMIT:
        raise SearchValidationError("invalid_limit")
    project = _authorize(db, organization_id, project_id, actor_user_id)
    fingerprint = _cursor_fingerprint(
        organization_id=organization_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        filters=filters,
    )
    decoded = _decode_cursor(cursor, fingerprint) if cursor else None
    collected, scan_truncated = _collect(db, project, organization_id, filters)
    items = [item for item in collected if _after_cursor(item, decoded)]
    items.sort(key=lambda item: (-item["sort_at"].timestamp(), item["entity_type"], item["entity_id"]))
    page = items[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = _encode_cursor(page[-1], fingerprint) if has_more and page else None
    for item in page:
        item.pop("sort_at")
    return {
        "items": page,
        "next_cursor": next_cursor,
        "limit": limit,
        "scan_truncated": scan_truncated,
        "scan_cap_per_type": MAX_SCAN_ROWS_PER_TYPE,
        "external_actions_created": False,
    }


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 200):
        raise SearchValidationError("invalid_name")
    return name.strip()


def _history(row: SavedSearchView, event: str) -> SavedSearchViewHistory:
    return SavedSearchViewHistory(
        view_id=row.id,
        organization_id=row.organization_id,
        project_id=row.project_id,
        owner_user_id=row.owner_user_id,
        sequence=row.record_version,
        event=event,
        resulting_version=row.record_version,
        snapshot={"name": row.name, "filters": row.filters, "state": row.state},
    )


def create_saved_view(db: Session, *, organization_id: int, project_id: int, actor_user_id: int,
                      name: str, filters: SearchFilters) -> SavedSearchView:
    _authorize(db, organization_id, project_id, actor_user_id)
    row = SavedSearchView(
        organization_id=organization_id,
        project_id=project_id,
        owner_user_id=actor_user_id,
        name=_safe_name(name),
        filters=filters.mapping(),
        state="active",
        record_version=1,
    )
    db.add(row)
    db.flush()
    db.add(_history(row, "created"))
    db.add(AuditLog(action="saved_search_view_created", entity_type="saved_search_view", entity_id=row.id,
                    details=f"project_id={project_id}; owner_user_id={actor_user_id}; filter_keys={','.join(sorted(FILTER_KEYS))}"))
    db.flush()
    return row


def _owned_view(db, *, organization_id, project_id, actor_user_id, view_id):
    row = db.scalar(select(SavedSearchView).where(
        SavedSearchView.id == view_id,
        SavedSearchView.organization_id == organization_id,
        SavedSearchView.project_id == project_id,
    ))
    if row is None or row.owner_user_id != actor_user_id:
        raise SearchDenied("scope_unavailable")
    return row


def list_saved_views(db: Session, *, organization_id: int, project_id: int,
                     actor_user_id: int) -> list[SavedSearchView]:
    try:
        _authorize(db, organization_id, project_id, actor_user_id)
    except SearchDenied:
        return []
    return list(db.scalars(select(SavedSearchView).where(
        SavedSearchView.organization_id == organization_id,
        SavedSearchView.project_id == project_id,
        SavedSearchView.owner_user_id == actor_user_id,
        SavedSearchView.state == "active",
    ).order_by(SavedSearchView.name, SavedSearchView.id)))


def update_saved_view(db: Session, *, organization_id: int, project_id: int, actor_user_id: int,
                      view_id: int, expected_version: int, name: str,
                      filters: SearchFilters) -> SavedSearchView:
    _authorize(db, organization_id, project_id, actor_user_id)
    row = _owned_view(db, organization_id=organization_id, project_id=project_id,
                      actor_user_id=actor_user_id, view_id=view_id)
    if row.state != "active":
        raise SearchValidationError("view_deleted")
    result = db.execute(update(SavedSearchView).where(
        SavedSearchView.id == row.id,
        SavedSearchView.owner_user_id == actor_user_id,
        SavedSearchView.record_version == expected_version,
        SavedSearchView.state == "active",
    ).values(name=_safe_name(name), filters=filters.mapping(), record_version=expected_version + 1)
      .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise SearchValidationError("version_conflict")
    db.expire(row)
    db.refresh(row)
    db.add(_history(row, "updated"))
    db.add(AuditLog(action="saved_search_view_updated", entity_type="saved_search_view", entity_id=row.id,
                    details=f"project_id={project_id}; owner_user_id={actor_user_id}; version={row.record_version}"))
    db.flush()
    return row


def delete_saved_view(db: Session, *, organization_id: int, project_id: int, actor_user_id: int,
                      view_id: int, expected_version: int) -> SavedSearchView:
    _authorize(db, organization_id, project_id, actor_user_id)
    row = _owned_view(db, organization_id=organization_id, project_id=project_id,
                      actor_user_id=actor_user_id, view_id=view_id)
    result = db.execute(update(SavedSearchView).where(
        SavedSearchView.id == row.id,
        SavedSearchView.owner_user_id == actor_user_id,
        SavedSearchView.record_version == expected_version,
        SavedSearchView.state == "active",
    ).values(state="deleted", record_version=expected_version + 1).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise SearchValidationError("version_conflict")
    db.expire(row)
    db.refresh(row)
    db.add(_history(row, "deleted"))
    db.add(AuditLog(action="saved_search_view_deleted", entity_type="saved_search_view", entity_id=row.id,
                    details=f"project_id={project_id}; owner_user_id={actor_user_id}; version={row.record_version}"))
    db.flush()
    return row


def get_saved_view_history(db: Session, *, organization_id: int, project_id: int, actor_user_id: int,
                           view_id: int) -> list[SavedSearchViewHistory]:
    _authorize(db, organization_id, project_id, actor_user_id)
    _owned_view(db, organization_id=organization_id, project_id=project_id,
                actor_user_id=actor_user_id, view_id=view_id)
    return list(db.scalars(select(SavedSearchViewHistory).where(
        SavedSearchViewHistory.view_id == view_id,
        SavedSearchViewHistory.organization_id == organization_id,
        SavedSearchViewHistory.project_id == project_id,
        SavedSearchViewHistory.owner_user_id == actor_user_id,
    ).order_by(SavedSearchViewHistory.sequence)))
