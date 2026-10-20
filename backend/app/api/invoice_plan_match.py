from __future__ import annotations

import os
import re
from datetime import date
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

AI_BONUS = 60
AI_MIN_CONFIDENCE = 0.6
AI_MAX_CANDIDATES = 80
_AI_CACHE: dict[tuple, dict | None] = {}

_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "plan_entry_id": {"type": ["integer", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["plan_entry_id", "confidence", "reason"],
}

_AI_SYSTEM_PROMPT = (
    "Ты финансовый контролёр строительного проекта. Подбери к счёту одну строку "
    "планового ДДС по смыслу: что покупается или за какие работы, раздел (ЭМ, АСДУ, СКТП, "
    "АР, ПНР и т.п.), объект, контрагент, сумма. Строки плана — укрупнённые статьи, поэтому "
    "выбирай БЛИЖАЙШУЮ по смыслу статью: например, комплектующие к автоматам и щитам — "
    "материалы ЭМ или ЭМ Щиты; двери — АР. Если объект в счёте не указан, выбери любую "
    "подходящую статью и скажи об этом в reason. plan_entry_id = null (это «Дополнительные "
    "затраты») — только если счёт явно не относится ни к одной статье (другой вид затрат). "
    "confidence — твоя уверенность от 0 до 1. reason — одно короткое предложение по-русски. "
    "Ответ строго JSON."
)

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
    tokens: set[str] = set()
    for t in normalized.split():
        if len(t) >= 4:
            tokens.add(t[:5])
    return tokens


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


def _ai_enabled() -> bool:
    raw = os.environ.get("PLAN_MATCH_AI", "true")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _build_ai_user_prompt(
    invoice: dict, candidates: list[dict]
) -> str:
    lines: list[str] = []
    lines.append("Счёт:")
    lines.append(f"контрагент: {invoice.get('counterparty') or '—'}")
    lines.append(f"назначение платежа: {invoice.get('payment_purpose') or '—'}")
    quote = invoice.get("payment_purpose_evidence_quote")
    if quote:
        lines.append(f"цитата назначения: {quote}")
    lines.append(f"сумма: {invoice.get('amount') or '—'}")
    lines.append(f"валюта: {invoice.get('currency') or '—'}")
    lines.append("")
    lines.append("Строки плана:")
    for c in candidates:
        cid = c.get("id")
        title = c.get("title") or ""
        category = c.get("category") or "—"
        cp = c.get("counterparty") or "—"
        remaining = c.get("remaining") or "—"
        month = c.get("month") or "—"
        lines.append(
            f"id={cid} | {title} | категория: {category} | контрагент: {cp} "
            f"| остаток: {remaining} | месяц: {month}"
        )
    return "\n".join(lines)


def _ai_pick(invoice: dict, candidates: list[dict]) -> dict | None:
    try:
        from app import gemini_analysis  # noqa: F401

        if not gemini_analysis.deepseek_configured():
            return None
        user_prompt = _build_ai_user_prompt(invoice, candidates)
        result = gemini_analysis._try_deepseek(
            _AI_SYSTEM_PROMPT, user_prompt, _AI_SCHEMA
        )
        if isinstance(result, dict):
            return result
        return None
    except Exception:
        return None


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

    entry_meta: dict[int, dict[str, Any]] = {}
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

        entry_meta[entry.id] = {
            "plan_entry_id": entry.id,
            "title": entry.title,
            "counterparty": entry.counterparty,
            "category": entry.category,
            "schedule_item_id": entry.schedule_item_id,
            "planned_amount": _money(planned_amount),
            "remaining": _money(remaining),
            "planned_date": entry.planned_date,
            "remaining_raw": remaining,
        }

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

    ai_used = False
    ai_reason: str | None = None

    if _ai_enabled():
        ai_candidates = _select_ai_candidates(entry_meta, suggestions, invoice_amount)
        cache_key = (
            proposal.id,
            str(invoice_amount),
            tuple(sorted(c["id"] for c in ai_candidates)),
        )
        if cache_key in _AI_CACHE:
            ai_result = _AI_CACHE[cache_key]
        else:
            invoice_payload = {
                "counterparty": invoice_cp,
                "payment_purpose": invoice_purpose,
                "payment_purpose_evidence_quote": getattr(
                    proposal, "payment_purpose_evidence_quote", None
                ),
                "amount": _money(invoice_amount),
                "currency": proposal.currency,
            }
            ai_result = _ai_pick(invoice_payload, ai_candidates)
            _AI_CACHE[cache_key] = ai_result

        if isinstance(ai_result, dict):
            ai_used = True
            try:
                picked_id = ai_result.get("plan_entry_id")
                confidence = float(ai_result.get("confidence") or 0.0)
            except Exception:
                picked_id = None
                confidence = 0.0
            reason_text = str(ai_result.get("reason") or "")

            candidate_ids = {c["id"] for c in ai_candidates}

            if (
                picked_id is not None
                and confidence >= AI_MIN_CONFIDENCE
                and picked_id in candidate_ids
            ):
                target = None
                for row in suggestions:
                    if row["plan_entry_id"] == picked_id:
                        target = row
                        break
                if target is None:
                    meta = entry_meta.get(picked_id)
                    if meta is not None:
                        target = {
                            "plan_entry_id": meta["plan_entry_id"],
                            "title": meta["title"],
                            "counterparty": meta["counterparty"],
                            "category": meta["category"],
                            "schedule_item_id": meta["schedule_item_id"],
                            "planned_amount": meta["planned_amount"],
                            "remaining": meta["remaining"],
                            "score": AI_BONUS,
                            "reasons": [],
                        }
                        suggestions.append(target)
                if target is not None:
                    if target["score"] < AI_BONUS:
                        target["score"] = AI_BONUS
                    else:
                        target["score"] += AI_BONUS
                    target["reasons"].append(f"По смыслу: {reason_text}")
            elif (
                picked_id is None
                and confidence >= AI_MIN_CONFIDENCE
            ):
                ai_reason = reason_text

    suggestions.sort(key=lambda row: (-row["score"], row["plan_entry_id"]))
    top = suggestions[:TOP_LIMIT]

    recommended: int | None = None
    for row in top:
        if row["score"] >= RECOMMENDED_SCORE:
            recommended = row["plan_entry_id"]
            break

    best_score = top[0]["score"] if top else 0
    additional_recommended = best_score < RECOMMENDED_SCORE
    if ai_reason is not None and best_score < RECOMMENDED_SCORE:
        additional_recommended = True

    result: dict[str, Any] = {
        "suggestions": top,
        "recommended": recommended,
        "additional_recommended": additional_recommended,
        "ai_used": ai_used,
    }
    if ai_reason is not None:
        result["ai_reason"] = ai_reason
    return result


def _select_ai_candidates(
    entry_meta: dict[int, dict[str, Any]],
    suggestions: list[dict[str, Any]],
    invoice_amount: Decimal,
) -> list[dict[str, Any]]:
    scores_by_id = {row["plan_entry_id"]: row["score"] for row in suggestions}

    groups: dict[str, list[dict[str, Any]]] = {}
    for meta in entry_meta.values():
        remaining_raw = meta["remaining_raw"]
        if remaining_raw <= ZERO:
            continue
        key = _normalize(meta["title"])
        groups.setdefault(key, []).append(meta)

    selected: list[dict[str, Any]] = []
    for group in groups.values():
        covering = [m for m in group if m["remaining_raw"] >= invoice_amount]
        if covering:
            covering.sort(
                key=lambda m: (
                    m["planned_date"] or date.min,
                    m["plan_entry_id"],
                )
            )
            chosen = covering[0]
        else:
            chosen = max(
                group, key=lambda m: (m["remaining_raw"], -m["plan_entry_id"])
            )
        selected.append(chosen)

    if len(selected) > AI_MAX_CANDIDATES:
        selected.sort(
            key=lambda m: (
                -scores_by_id.get(m["plan_entry_id"], 0),
                m["plan_entry_id"],
            )
        )
        selected = selected[:AI_MAX_CANDIDATES]

    def _month(m: dict[str, Any]) -> str | None:
        pd = m.get("planned_date")
        if pd is None:
            return None
        return f"{pd.year:04d}-{pd.month:02d}"

    return [
        {
            "id": m["plan_entry_id"],
            "title": m["title"],
            "category": m["category"],
            "counterparty": m["counterparty"],
            "remaining": m["remaining"],
            "month": _month(m),
        }
        for m in selected
    ]


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
