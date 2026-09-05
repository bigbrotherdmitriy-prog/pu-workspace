from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from decimal import Decimal

from app.execution_forecast.contracts import EntitySource, EvidencePin, ForecastInput, ScheduleFact


LOW_CONFIDENCE = 0.70


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _safe_evidence(pin: EvidencePin) -> dict:
    result = {
        "evidence_id": pin.evidence_id,
        "source_version_id": pin.source_version_id,
        "confidence": pin.confidence,
        "verification": pin.verification,
    }
    if pin.page is not None:
        result["page"] = pin.page
    if pin.coordinates is not None:
        result["coordinates"] = list(pin.coordinates)
    return result


def _source_payload(source: EntitySource) -> dict:
    return {
        "entity_type": source.entity_type,
        "entity_id": source.entity_id,
        "fields": list(source.fields),
        "state": source.state,
        "evidence": [_safe_evidence(pin) for pin in source.evidence],
        "evidence_exact": bool(source.evidence),
    }


def _evidence_score(source: EntitySource) -> float:
    if not source.evidence:
        return 0.45
    scores = []
    for pin in source.evidence:
        confidence = pin.confidence if pin.confidence is not None else 0.45
        if pin.verification == "verified":
            confidence = max(confidence, 0.9)
        else:
            confidence = min(confidence, 0.69)
        scores.append(confidence)
    return min(scores)


def _combine_confidence(base: float, source: EntitySource) -> float:
    # Evidence cannot make an uncertain business state certain.  It can only
    # corroborate the exact inputs from which the deterministic formula ran.
    if not source.evidence:
        return round(min(base * 0.8, 0.65), 3)
    return round(min(base, (base * 0.7) + (_evidence_score(source) * 0.3)), 3)


def _schedule_projection(item: ScheduleFact, as_of: date) -> tuple[date | None, str, float]:
    if not 0 <= item.actual_progress <= 100:
        return None, "invalid_actual_progress", 0.1
    if item.actual_finish is not None:
        return item.actual_finish, "actual_finish", 0.98
    if item.actual_progress >= 100:
        return as_of, "completed_progress_as_of", 0.86
    if item.actual_start is not None and item.actual_start <= as_of and 0 < item.actual_progress < 100:
        elapsed_days = max(1, (as_of - item.actual_start).days + 1)
        remaining_days = math.ceil(elapsed_days * (100 - item.actual_progress) / item.actual_progress)
        return as_of + timedelta(days=remaining_days), "linear_progress_extrapolation", 0.76
    if item.planned_finish is not None:
        return item.planned_finish, "approved_plan_finish", 0.58
    return None, "insufficient_schedule_data", 0.2


def _risk(code: str, severity: str, explanation: str, sources: list[dict]) -> dict:
    return {"code": code, "severity": severity, "explanation": explanation, "sources": sources}


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_forecast(data: ForecastInput) -> dict:
    """Build an advisory forecast without persistence or external actions."""
    schedule_rows: list[dict] = []
    cash_rows: list[dict] = []
    budget_rows: list[dict] = []
    risks: list[dict] = []
    confidence_samples: list[float] = []

    for item in sorted(data.schedule, key=lambda row: row.id):
        predicted, method, base = _schedule_projection(item, data.as_of)
        confidence = _combine_confidence(base, item.source)
        source = _source_payload(item.source)
        row_risks: list[str] = []
        if predicted is None:
            row_risks.append("missing_finish")
        elif item.planned_finish is not None and predicted > item.planned_finish:
            row_risks.append("predicted_delay")
            risks.append(_risk(
                "schedule_delay", "high",
                f"Этап #{item.id}: прогнозная дата позже плановой.", [source],
            ))
        if confidence < LOW_CONFIDENCE:
            row_risks.append("low_confidence")
        schedule_rows.append({
            "id": item.id,
            "title": item.title,
            "planned_finish": item.planned_finish.isoformat() if item.planned_finish else None,
            "predicted_finish": predicted.isoformat() if predicted else None,
            "actual_progress": item.actual_progress,
            "formula": method,
            "formula_description": {
                "actual_finish": "прогноз = подтверждённый факт завершения",
                "completed_progress_as_of": "прогноз = дата среза при факте 100%",
                "linear_progress_extrapolation": "осталось = ceil(прошло дней × (100 - факт %) / факт %)",
                "approved_plan_finish": "нет факта: прогноз = дата утверждённого плана",
                "insufficient_schedule_data": "недостаточно данных для расчёта",
                "invalid_actual_progress": "факт вне диапазона 0–100%; расчёт заблокирован",
            }[method],
            "confidence": confidence,
            "risks": row_risks,
            "sources": [source],
        })
        confidence_samples.append(confidence)

    balance = Decimal("0")
    minimum_balance = Decimal("0")
    gap_date: date | None = None
    cash_events = sorted(
        data.cash_flow,
        key=lambda row: (row.actual_date or row.planned_date, row.id),
    )
    for item in cash_events:
        is_actual = item.status in {"paid", "received"} and item.actual_date is not None
        event_date = item.actual_date if is_actual else item.planned_date
        amount = item.actual_amount if is_actual else item.planned_amount
        signed = amount if item.direction == "inflow" else -amount
        balance += signed
        if balance < minimum_balance:
            minimum_balance = balance
            gap_date = event_date
        base = 0.98 if is_actual else (0.82 if item.status in {"approved", "active"} else 0.46)
        confidence = _combine_confidence(base, item.source)
        source = _source_payload(item.source)
        row_risks = []
        if not is_actual and item.status == "proposed":
            row_risks.append("unconfirmed_plan")
        if confidence < LOW_CONFIDENCE:
            row_risks.append("low_confidence")
        cash_rows.append({
            "id": item.id,
            "title": item.title,
            "date": event_date.isoformat(),
            "direction": item.direction,
            "amount": _money(amount),
            "value_kind": "actual" if is_actual else "planned",
            "running_balance": _money(balance),
            "confidence": confidence,
            "risks": row_risks,
            "sources": [source],
        })
        confidence_samples.append(confidence)
    if minimum_balance < 0:
        risks.append(_risk(
            "cash_gap", "critical",
            f"Плановый накопительный остаток снижается до {_money(minimum_balance)}.",
            [row["sources"][0] for row in cash_rows],
        ))

    planned_total = Decimal("0")
    forecast_total = Decimal("0")
    for item in sorted(data.budget, key=lambda row: row.id):
        # Conservative estimate-at-completion: never hide a known commitment,
        # actual spend or a larger declared forecast behind the original plan.
        estimate = max(
            item.planned_amount,
            item.committed_amount,
            item.actual_amount,
            item.declared_forecast_amount,
        )
        variance = estimate - item.planned_amount
        base = 0.84 if item.status in {"approved", "active", "closed"} else 0.48
        confidence = _combine_confidence(base, item.source)
        source = _source_payload(item.source)
        budget_rows.append({
            "id": item.id,
            "description": item.description,
            "currency": item.currency,
            "planned_amount": _money(item.planned_amount),
            "forecast_amount": _money(estimate),
            "variance": _money(variance),
            "formula": "max(plan, committed, actual, declared_forecast)",
            "confidence": confidence,
            "sources": [source],
        })
        if variance > 0:
            risks.append(_risk(
                "budget_overrun", "high",
                f"Строка бюджета #{item.id}: прогноз выше плана на {_money(variance)} {item.currency}.", [source],
            ))
        planned_total += item.planned_amount
        forecast_total += estimate
        confidence_samples.append(confidence)

    for task in sorted(data.tasks, key=lambda row: row.id):
        if task.due_date and task.due_date < data.as_of and task.status not in {"completed", "cancelled"}:
            risks.append(_risk(
                "overdue_task", "high", f"Задача #{task.id} просрочена и может повлиять на сроки.",
                [_source_payload(task.source)],
            ))

    planned_inflow = sum((row.planned_amount for row in data.cash_flow if row.direction == "inflow"), Decimal("0"))
    for contract in sorted(data.contracts, key=lambda row: row.id):
        if contract.status == "active" and contract.amount and planned_inflow == 0:
            risks.append(_risk(
                "contract_without_cash_inflow", "medium",
                f"Договор #{contract.id} активен, но в ДДС нет плана поступлений.",
                [_source_payload(contract.source)],
            ))

    overall_confidence = round(sum(confidence_samples) / len(confidence_samples), 3) if confidence_samples else 0.0
    input_summary = {
        "project_id": data.project_id,
        "organization_id": data.organization_id,
        "as_of": data.as_of.isoformat(),
        "schedule": schedule_rows,
        "budget": budget_rows,
        "cash_flow": cash_rows,
        "risk_codes": [row["code"] for row in risks],
    }
    forecast_id = _fingerprint(input_summary)
    return {
        "forecast_id": forecast_id,
        "project_id": data.project_id,
        "as_of": data.as_of.isoformat(),
        "publication_state": "draft",
        "advisory_only": True,
        "can_trigger_actions": False,
        "requires_human_confirmation": True,
        "confidence": {
            "score": overall_confidence,
            "band": "high" if overall_confidence >= 0.85 else "medium" if overall_confidence >= LOW_CONFIDENCE else "low",
            "formula": "arithmetic_mean(schedule, budget, cash-flow input confidence)",
            "low_confidence_threshold": LOW_CONFIDENCE,
        },
        "schedule": {
            "formula": "actual finish; else linear fact extrapolation; else approved plan",
            "predicted_finish": max(
                (row["predicted_finish"] for row in schedule_rows if row["predicted_finish"]),
                default=None,
            ),
            "stages": schedule_rows,
        },
        "budget": {
            "formula": "sum(max(plan, committed, actual, declared_forecast))",
            "planned_total": _money(planned_total),
            "forecast_total": _money(forecast_total),
            "variance": _money(forecast_total - planned_total),
            "lines": budget_rows,
        },
        "cash_flow": {
            "formula": "running_balance(d) = Σ actual_or_planned_inflow - Σ actual_or_planned_outflow",
            "opening_balance": "0.00",
            "closing_balance": _money(balance),
            "minimum_balance": _money(minimum_balance),
            "cash_gap_date": gap_date.isoformat() if gap_date and minimum_balance < 0 else None,
            "events": cash_rows,
        },
        "risks": risks,
        "manual_confirmation": {
            "binding": forecast_id,
            "required_before": ["publish_forecast", "change_plan", "financial_action", "external_action"],
            "reason": "Forecast is deterministic advice, not a verified fact or authorization.",
            "persistence_available": False,
        },
    }
