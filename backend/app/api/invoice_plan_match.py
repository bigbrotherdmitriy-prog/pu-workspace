from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.execution_finance import CashFlowEntry, InvoiceExtractionProposal
from app.models.user import User

router = APIRouter(prefix="/projects", tags=["invoice-plan-match"])

OUTFLOW_DIRECTION = "outflow"
ZERO = Decimal("0.00")
MIN_SCORE = 30
RECOMMENDED_SCORE = 50
TOP_LIMIT = 5

_LEGAL_FORMS = ("ооо", "ип", "ао", "пао", "зао", "оао", "нко")
_QUOTES_RE = re.compile(r"[\"«»'`]+")
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower()
    text = _QUOTES_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    tokens = [t for t in text.split() if t not in _LEGAL_FORMS]
    return " ".join(tokens)


def _counterparty_matches(invoice_cp: str | None, entry_cp: str | None) -> bool:
    a = _normalize(invoice_cp)
    b = _normalize(entry_cp)
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _inn_matches(inn: str | None, entry_text: str | None) -> bool:
    if not inn:
        return False
    digits = re.sub(r"\D", "", inn)
    if len(digits) < 10:
        return False
    haystack = re.sub(r"\D", "", entry_text or "")
    if not haystack:
        return False
    return digits in haystack


def _word_tokens(value: str | None) -> set[str]:
    normalized = _normalize(value)
    return {t for t in normalized.split() if len(t) >= 4}


def _purpose_overlap(purpose: str | None, *sources: str | None) -> bool:
    purpose_tokens = _word_tokens(purpose)
    if not purpose_tokens:
        return False
    for src in sources:
        if not src:
            continue
        if purpose_tokens & _word_tokens(src):
            return True
    return False


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _extract_inn(proposal: InvoiceExtractionProposal) -> str | None:
    candidates = [
        getattr(proposal, "counterparty_inn", None),
        getattr(proposal, "inn", None),
        getattr(proposal, "supplier_inn", None),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    haystack = " ".join(
        [proposal.payment_purpose or "", proposal.counterparty or ""]
    )
    match = re.search(r"(?:ИНН[:\s]*)?(\d{10,12})", haystack)
    if match:
        return match.group(1)
    return None


@router.get(
    "/{project_id}/invoice-extraction-proposals/{proposal_id}/plan-line-suggestions"
)
def get_plan_line_suggestions(
    project_id: int,
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict[str, Any]:
    require_project_role(db, user, project_id, "viewer")

    proposal = db.execute(
        select(InvoiceExtractionProposal).where(
            InvoiceExtractionProposal.id == proposal_id,
            InvoiceExtractionProposal.project_id == project_id,
        )
    ).scalar_one_or_none()
    if proposal is None:
        raise HTTPException(404, "Invoice extraction proposal not found")

    entries = list(
        db.execute(
            select(CashFlowEntry).where(
                CashFlowEntry.project_id == project_id,
                CashFlowEntry.direction == OUTFLOW_DIRECTION,
                CashFlowEntry.planned_amount > 0,
            )
        ).scalars()
    )

    invoiced_totals = _collect_invoiced_totals(db, entries)

    invoice_amount = _to_decimal(proposal.amount)
    invoice_cp = proposal.counterparty
    invoice_purpose = proposal.payment_purpose
    invoice_contract_id = getattr(proposal, "contract_id", None)
    invoice_inn = _extract_inn(proposal)

    suggestions: list[dict[str, Any]] = []
    for entry in entries:
        planned_amount = _to_decimal(entry.planned_amount)
        invoiced = invoiced_totals.get(entry.id, ZERO)
        remaining = planned_amount - invoiced
        if remaining < ZERO:
            remaining = ZERO

        score = 0
        reasons: list[str] = []

        if _counterparty_matches(invoice_cp, entry.counterparty):
            score += 50
            reasons.append("Совпадает контрагент")
        elif _inn_matches(invoice_inn, entry.title) or _inn_matches(
            invoice_inn, entry.counterparty
        ):
            score += 50
            reasons.append("Совпадает ИНН")

        if _purpose_overlap(invoice_purpose, entry.title, entry.category):
            score += 20
            reasons.append("Слова из назначения платежа совпадают с наименованием/категорией")

        if invoice_amount > ZERO and invoice_amount <= remaining:
            score += 20
            reasons.append("Сумма счёта покрывается остатком строки")

        entry_contract_id = getattr(entry, "contract_id", None)
        if (
            invoice_contract_id is not None
            and entry_contract_id is not None
            and invoice_contract_id == entry_contract_id
        ):
            score += 10
            reasons.append("Совпадает договор")

        if score < MIN_SCORE:
            continue

        suggestions.append(
            {
                "plan_entry_id": entry.id,
                "title": entry.title,
                "counterparty": entry.counterparty,
                "category": entry.category,
                "schedule_item_id": entry.schedule_item_id,
                "planned_amount": _money(planned_amount),
                "remaining": _money(remaining),
                "score": score,
                "reasons": reasons,
            }
        )

    suggestions.sort(key=lambda row: (-row["score"], row["plan_entry_id"]))
    top = suggestions[:TOP_LIMIT]

    recommended: int | None = None
    for row in top:
        if row["score"] >= RECOMMENDED_SCORE:
            recommended = row["plan_entry_id"]
            break

    best_score = top[0]["score"] if top else 0
    additional_recommended = best_score < RECOMMENDED_SCORE

    return {
        "suggestions": top,
        "recommended": recommended,
        "additional_recommended": additional_recommended,
    }


def _collect_invoiced_totals(
    db: Session, entries: list[CashFlowEntry]
) -> dict[int, Decimal]:
    entry_ids = [entry.id for entry in entries]
    totals: dict[int, Decimal] = {entry_id: ZERO for entry_id in entry_ids}
    if not entry_ids:
        return totals
    if not hasattr(CashFlowEntry, "plan_entry_id"):
        return totals

    plan_entry_column = getattr(CashFlowEntry, "plan_entry_id", None)
    if plan_entry_column is None:
        return totals

    has_invoiced = hasattr(CashFlowEntry, "invoiced_amount")
    amount_column = (
        getattr(CashFlowEntry, "invoiced_amount", None)
        if has_invoiced
        else getattr(CashFlowEntry, "actual_amount", None)
    )
    if amount_column is None:
        return totals

    rows = db.execute(
        select(plan_entry_column, amount_column).where(
            plan_entry_column.in_(entry_ids)
        )
    ).all()
    for plan_entry_id, amount in rows:
        if plan_entry_id is None:
            continue
        current = totals.get(plan_entry_id, ZERO)
        totals[plan_entry_id] = current + _to_decimal(amount)
    return totals


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"
