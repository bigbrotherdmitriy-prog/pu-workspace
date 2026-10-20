from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.dds_plan_version import DdsPlanVersion
from app.models.execution_finance import CashFlowEntry
from app.models.user import User


router = APIRouter(prefix="/projects", tags=["dds-plan-versions"])

EXCLUDED_STATUSES = ("cancelled", "deleted", "rejected")
INITIAL_LABEL = "Исходный план"


class DdsPlanVersionCreate(BaseModel):
    contract_id: int | None = None
    label: str = Field(min_length=1, max_length=200)
    effective_from: date | None = None


def _money(value) -> str:
    if value is None:
        return "0.00"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _rows_for(db: Session, project_id: int, contract_id: int | None) -> list[dict]:
    stmt = (
        select(CashFlowEntry)
        .where(CashFlowEntry.project_id == project_id)
        .where(~CashFlowEntry.status.in_(EXCLUDED_STATUSES))
        .where(CashFlowEntry.planned_amount > 0)
    )
    # Without a contract the version is a snapshot of the whole project plan.
    if contract_id is not None:
        stmt = stmt.where(CashFlowEntry.contract_id == contract_id)
    stmt = stmt.order_by(CashFlowEntry.id)
    rows: list[dict] = []
    for entry in db.scalars(stmt).all():
        rows.append(
            {
                "id": entry.id,
                "title": entry.title,
                "direction": entry.direction,
                "planned_date": entry.planned_date.isoformat() if entry.planned_date else None,
                "planned_amount": _money(entry.planned_amount),
                "category": None,
                "cost_category_id": entry.cost_category_id,
                "budget_line_id": entry.budget_line_id,
                "status": entry.status,
            }
        )
    return rows


def _totals(rows: Iterable[dict]) -> dict:
    inflow = Decimal("0.00")
    outflow = Decimal("0.00")
    for row in rows:
        amount = Decimal(str(row.get("planned_amount") or "0"))
        if row.get("direction") == "inflow":
            inflow += amount
        elif row.get("direction") == "outflow":
            outflow += amount
    return {"inflow": _money(inflow), "outflow": _money(outflow)}


def _next_version_number(db: Session, project_id: int, contract_id: int | None) -> int:
    stmt = select(func.max(DdsPlanVersion.version_number)).where(DdsPlanVersion.project_id == project_id)
    if contract_id is None:
        stmt = stmt.where(DdsPlanVersion.contract_id.is_(None))
    else:
        stmt = stmt.where(DdsPlanVersion.contract_id == contract_id)
    current = db.scalar(stmt)
    return (current or 0) + 1


def _create_version(
    db: Session,
    project_id: int,
    contract_id: int | None,
    label: str,
    effective_from: date | None,
    user_id: int | None,
    rows: list[dict] | None = None,
) -> DdsPlanVersion:
    if rows is None:
        rows = _rows_for(db, project_id, contract_id)
    version_number = _next_version_number(db, project_id, contract_id)
    version = DdsPlanVersion(
        project_id=project_id,
        contract_id=contract_id,
        version_number=version_number,
        label=label,
        effective_from=effective_from,
        created_by_user_id=user_id,
        snapshot=rows,
        totals=_totals(rows),
    )
    db.add(version)
    db.flush()
    db.add(
        AuditLog(
            action="dds_plan_version_created",
            entity_type="dds_plan_version",
            entity_id=version.id,
            details=f"project_id={project_id}; version={version_number}; contract_id={contract_id}; user_id={user_id}; label={label}",
        )
    )
    return version


def ensure_initial_version(
    db: Session,
    project_id: int,
    contract_id: int | None,
    user_id: int | None,
) -> DdsPlanVersion | None:
    stmt = select(func.count(DdsPlanVersion.id)).where(DdsPlanVersion.project_id == project_id)
    if contract_id is None:
        stmt = stmt.where(DdsPlanVersion.contract_id.is_(None))
    else:
        stmt = stmt.where(DdsPlanVersion.contract_id == contract_id)
    existing = db.scalar(stmt) or 0
    if existing > 0:
        return None
    rows = _rows_for(db, project_id, contract_id)
    if not rows:
        return None
    return _create_version(db, project_id, contract_id, INITIAL_LABEL, None, user_id, rows=rows)


def _article_key(row: dict) -> tuple:
    budget_line_id = row.get("budget_line_id")
    if budget_line_id is not None:
        return ("bl", budget_line_id)
    title = (row.get("title") or "").strip().casefold()
    return ("td", row.get("direction"), title)


def _aggregate(rows: Iterable[dict]) -> dict[tuple, dict]:
    result: dict[tuple, dict] = {}
    for row in rows:
        key = _article_key(row)
        planned_date = row.get("planned_date")
        month = planned_date[:7] if planned_date else ""
        amount = Decimal(str(row.get("planned_amount") or "0"))
        bucket = result.setdefault(
            key,
            {
                "title": row.get("title") or "",
                "direction": row.get("direction") or "",
                "total": Decimal("0"),
                "months": {},
            },
        )
        bucket["total"] += amount
        if month:
            bucket["months"][month] = bucket["months"].get(month, Decimal("0")) + amount
    return result


@router.post("/{project_id}/dds-plan-versions")
def create_dds_plan_version(
    project_id: int,
    payload: DdsPlanVersionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "manager")
    ensure_initial_version(db, project_id, payload.contract_id, getattr(user, "id", None))
    user_id = getattr(user, "id", None)
    version = _create_version(
        db,
        project_id,
        payload.contract_id,
        payload.label,
        payload.effective_from,
        user_id,
    )
    rows_count = len(version.snapshot or [])
    db.commit()
    db.refresh(version)
    return {
        "id": version.id,
        "version_number": version.version_number,
        "label": version.label,
        "effective_from": version.effective_from.isoformat() if version.effective_from else None,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "totals": version.totals or {"inflow": "0.00", "outflow": "0.00"},
        "rows_count": rows_count,
    }


@router.get("/{project_id}/dds-plan-versions")
def list_dds_plan_versions(
    project_id: int,
    contract_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    stmt = select(DdsPlanVersion).where(DdsPlanVersion.project_id == project_id)
    if contract_id is None:
        stmt = stmt.where(DdsPlanVersion.contract_id.is_(None))
    else:
        stmt = stmt.where(DdsPlanVersion.contract_id == contract_id)
    stmt = stmt.order_by(DdsPlanVersion.version_number)
    items = []
    for version in db.scalars(stmt).all():
        items.append(
            {
                "id": version.id,
                "version_number": version.version_number,
                "label": version.label,
                "effective_from": version.effective_from.isoformat() if version.effective_from else None,
                "created_at": version.created_at.isoformat() if version.created_at else None,
                "totals": version.totals or {"inflow": "0.00", "outflow": "0.00"},
            }
        )
    return items


@router.get("/{project_id}/dds-plan-versions/compare")
def compare_dds_plan_versions(
    project_id: int,
    base: int = Query(...),
    target: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    base_version = db.get(DdsPlanVersion, base)
    if base_version is None or base_version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Version not found")

    if target == "current":
        target_rows = _rows_for(db, project_id, base_version.contract_id)
        target_label = "current"
    else:
        try:
            target_id = int(target)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid target")
        target_version = db.get(DdsPlanVersion, target_id)
        if target_version is None or target_version.project_id != project_id:
            raise HTTPException(status_code=404, detail="Version not found")
        target_rows = target_version.snapshot or []
        target_label = target_version.label

    base_agg = _aggregate(base_version.snapshot or [])
    target_agg = _aggregate(target_rows)

    all_keys = set(base_agg.keys()) | set(target_agg.keys())
    articles = []
    added: list[str] = []
    removed: list[str] = []

    for key in all_keys:
        base_bucket = base_agg.get(key)
        target_bucket = target_agg.get(key)
        if base_bucket is None and target_bucket is not None:
            added.append((target_bucket["title"] or "").strip())
        elif target_bucket is None and base_bucket is not None:
            removed.append((base_bucket["title"] or "").strip())
        source = base_bucket or target_bucket
        base_total = base_bucket["total"] if base_bucket else Decimal("0")
        target_total = target_bucket["total"] if target_bucket else Decimal("0")
        difference = target_total - base_total
        months: list[dict] = []
        month_keys = set()
        if base_bucket:
            month_keys |= set(base_bucket["months"].keys())
        if target_bucket:
            month_keys |= set(target_bucket["months"].keys())
        for month in sorted(month_keys):
            base_m = base_bucket["months"].get(month, Decimal("0")) if base_bucket else Decimal("0")
            target_m = target_bucket["months"].get(month, Decimal("0")) if target_bucket else Decimal("0")
            diff_m = target_m - base_m
            if base_m == 0 and target_m == 0:
                continue
            months.append(
                {
                    "month": month,
                    "base": _money(base_m),
                    "target": _money(target_m),
                    "difference": _money(diff_m),
                }
            )
        articles.append(
            {
                "title": (source["title"] or "").strip(),
                "direction": source["direction"],
                "base_total": _money(base_total),
                "target_total": _money(target_total),
                "difference": _money(difference),
                "months": months,
            }
        )

    def totals_for(agg: dict) -> dict:
        inflow = sum((b["total"] for b in agg.values() if b["direction"] == "inflow"), Decimal("0"))
        outflow = sum((b["total"] for b in agg.values() if b["direction"] == "outflow"), Decimal("0"))
        return {"inflow": inflow, "outflow": outflow}

    base_totals = totals_for(base_agg)
    target_totals = totals_for(target_agg)

    return {
        "base_id": base_version.id,
        "target_id": None if target == "current" else int(target),
        "target_label": target_label,
        "articles": articles,
        "totals": {
            "inflow": {
                "base": _money(base_totals["inflow"]),
                "target": _money(target_totals["inflow"]),
                "difference": _money(target_totals["inflow"] - base_totals["inflow"]),
            },
            "outflow": {
                "base": _money(base_totals["outflow"]),
                "target": _money(target_totals["outflow"]),
                "difference": _money(target_totals["outflow"] - base_totals["outflow"]),
            },
        },
        "added": added,
        "removed": removed,
    }
