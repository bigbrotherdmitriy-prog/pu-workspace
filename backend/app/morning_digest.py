from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.cash_gap import plan_cash_gaps
from app.database import SessionLocal
from app.jobs.queue import enqueue
from app.models.execution_finance import (
    BudgetLine,
    CashFlowEntry,
    InvoiceExtractionProposal,
)
from app.models.project import Project

DIGEST_KIND = "finance.morning_digest"
DIGEST_HOUR = int(os.getenv("PU_MORNING_DIGEST_HOUR", "9"))
DIGEST_TZ = os.getenv("PU_MORNING_DIGEST_TZ", "Europe/Moscow")

SKIP_STATUSES = ("cancelled", "deleted", "rejected")

MONTHS_NOM = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _enabled() -> bool:
    return os.getenv("PU_MORNING_DIGEST_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def _format_rub(value: Decimal) -> str:
    whole = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sign = "−" if value < 0 else ""
    s = f"{abs(int(whole)):,}".replace(",", " ")
    return f"{sign}{s} ₽"


def _month_label(month: str) -> str:
    year_s, month_s = month.split("-")
    idx = int(month_s) - 1
    name = MONTHS_NOM[idx] if 0 <= idx < 12 else month_s
    return f"{name} {year_s}"


def schedule_morning_digest(db, *, now: datetime) -> int:
    if not _enabled():
        return 0
    local = now.astimezone(ZoneInfo(DIGEST_TZ))
    if local.hour < DIGEST_HOUR:
        return 0
    day = local.date().isoformat()
    job = enqueue(db, DIGEST_KIND, {"day": day}, idempotency_key=f"{DIGEST_KIND}:{day}")
    return int(job.status == "queued" and job.attempts == 0)


def _project_stats(db, project: Project, today: date, since: datetime) -> dict:
    cash = list(db.scalars(
        select(CashFlowEntry).where(
            CashFlowEntry.project_id == project.id,
            CashFlowEntry.status.notin_(SKIP_STATUSES),
        )
    ))

    to_confirm = int(db.scalar(
        select(func.count()).select_from(InvoiceExtractionProposal).where(
            InvoiceExtractionProposal.project_id == project.id,
            InvoiceExtractionProposal.status == "proposed",
        )
    ) or 0)

    overdue_count = 0
    overdue_sum = Decimal("0")
    for row in cash:
        if row.direction != "outflow":
            continue
        invoiced = row.invoiced_amount or Decimal("0")
        if invoiced <= 0:
            continue
        if row.actual_date is not None:
            continue
        if row.planned_date is None or row.planned_date >= today:
            continue
        if row.status not in ("approved", "proposed"):
            continue
        overdue_count += 1
        overdue_sum += Decimal(invoiced)

    new_invoices = int(db.scalar(
        select(func.count()).select_from(InvoiceExtractionProposal).where(
            InvoiceExtractionProposal.project_id == project.id,
            InvoiceExtractionProposal.created_at >= since,
        )
    ) or 0)

    overspend_count = 0
    overspend_sum = Decimal("0")
    for line in db.scalars(
        select(BudgetLine).where(BudgetLine.project_id == project.id)
    ):
        planned = Decimal(line.planned_amount or 0)
        actual = Decimal(line.actual_amount or 0)
        if actual > planned:
            overspend_count += 1
            overspend_sum += (actual - planned)

    gap = plan_cash_gaps(cash)
    worst = gap.get("worst")

    return {
        "to_confirm": to_confirm,
        "overdue_count": overdue_count,
        "overdue_sum": overdue_sum,
        "new_invoices": new_invoices,
        "overspend_count": overspend_count,
        "overspend_sum": overspend_sum,
        "worst": worst,
    }


def build_morning_digest(db, *, today: date) -> str | None:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    projects = list(db.scalars(
        select(Project).where(Project.archived_at.is_(None)).order_by(Project.name)
    ))

    blocks: list[str] = []
    for project in projects:
        stats = _project_stats(db, project, today, since)
        has_any = (
            stats["to_confirm"] > 0
            or stats["overdue_count"] > 0
            or stats["new_invoices"] > 0
            or stats["overspend_count"] > 0
            or stats["worst"] is not None
        )
        if not has_any:
            continue
        lines: list[str] = [f"📁 {project.name}"]
        if stats["to_confirm"] > 0:
            lines.append(f"• Подтвердить счетов: {stats['to_confirm']}")
        if stats["overdue_count"] > 0:
            lines.append(
                f"• Просрочено счетов: {stats['overdue_count']} на {_format_rub(stats['overdue_sum'])}"
            )
        if stats["new_invoices"] > 0:
            lines.append(f"• Новых счетов за сутки: {stats['new_invoices']}")
        if stats["overspend_count"] > 0:
            lines.append(
                f"• Перерасход: {stats['overspend_count']} статей на {_format_rub(stats['overspend_sum'])}"
            )
        worst = stats["worst"]
        if worst is not None:
            amount = Decimal(worst["cumulative"])
            lines.append(
                f"• Кассовый разрыв: {_month_label(worst['month'])}, {_format_rub(amount)}"
            )
        blocks.append("\n".join(lines))

    if not blocks:
        return None
    header = f"☀️ Сводка на {today.strftime('%d.%m.%Y')}"
    return header + "\n\n" + "\n\n".join(blocks) + "\n"


def run_morning_digest_job(payload: dict) -> dict:
    from app.integrations.telegram import notify_telegram, telegram_configured

    today = date.fromisoformat(payload["day"])
    with SessionLocal() as db:
        text = build_morning_digest(db, today=today)
        projects_count = 0
        if text is not None:
            projects_count = sum(1 for line in text.splitlines() if line.startswith("📁 "))

    if text is None:
        return {"sent": False, "projects": 0}

    if not telegram_configured():
        return {"sent": False, "reason": "telegram_not_configured"}

    ok = bool(notify_telegram(text))
    return {"sent": ok, "projects": projects_count}
