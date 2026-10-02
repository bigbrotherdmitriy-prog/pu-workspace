"""Steps 1–3 of ADR-DDS-ARTICLE-BUDGET-RU: new proposals and read-only old links.

No legacy apply or forecast approval endpoint is provided by this stage.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Numeric, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role
from app.finance_money import project_currency
from app.finance_source_pins import assert_document_pin_current, resolve_current_document_pin
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, CostCategory, DdsArticleBudgetOperation,
    PaymentEvent,
)
from app.models.organization_contract import Contract
from app.models.project import Project
from app.structured_import import MATRIX_ROUNDING_ALGORITHM, parse_structured_rows


class ArticleBudgetPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)
    contract_id: int = Field(gt=0)
    plan_year: int = Field(ge=2000, le=2100)
    budget_revision: int = Field(ge=1, le=10000)
    mode: Literal["create_budget", "import_forecast"] = "create_budget"
    category_by_article: dict[int, int] = Field(default_factory=dict, max_length=500)


class ArticleBudgetApplyRequest(ArticleBudgetPreviewRequest):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_document_version_id: int = Field(gt=0)
    expected_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    owner_confirmed: bool = False


def normalize_article(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _snapshot(row) -> dict:
    result = {}
    for column in row.__table__.columns:
        if column.name in {"created_at", "updated_at"}:
            continue
        value = getattr(row, column.name)
        if value is not None and isinstance(column.type, Numeric):
            # Python defaults/just-flushed values and reloaded Numeric must hash identically.
            value = format(Decimal(str(value)), f".{column.type.scale or 0}f")
        elif isinstance(value, (Decimal, date, datetime)):
            value = str(value)
        result[column.name] = value
    return result


def _source_cash(db, payload, document_id):
    # Include *all* source versions: importing a new version cannot duplicate old money.
    return list(db.scalars(select(CashFlowEntry).where(
        CashFlowEntry.project_id == payload.project_id,
        CashFlowEntry.contract_id == payload.contract_id,
        CashFlowEntry.source_document_id == document_id,
        CashFlowEntry.direction == "outflow",
    ).order_by(CashFlowEntry.id)))


def _preview(document_id, payload, db):
    project = db.get(Project, payload.project_id)
    contract = db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.project_id == payload.project_id))
    if project is None or contract is None or contract.contract_kind == "prime_reference":
        raise HTTPException(422, "CONTRACT_SCOPE_MISMATCH: выберите финансовый договор проекта")
    try:
        currency = project_currency(db, project.id)
    except ValueError as exc:
        raise HTTPException(422, f"PROJECT_CURRENCY_INVALID: {exc}") from exc
    pin = resolve_current_document_pin(db, project.id, document_id)
    parsed = parse_structured_rows(pin.version.content or "", "cash-flow", source_name=pin.document.name,
                                   plan_year=payload.plan_year)
    conflicts = [{"code": "MATRIX_INVALID", "message": text} for text in parsed.get("blocking_issues", [])]
    if parsed.get("layout") != "monthly_matrix":
        conflicts.append({"code": "MATRIX_REQUIRED", "message": "Нужна матрица статья × месяц"})
    articles = [dict(article) for article in parsed.get("articles", []) if article["direction"] == "outflow"]
    if not articles:
        conflicts.append({"code": "NO_EXPENSE_ARTICLES", "message": "Расходные статьи не найдены"})
    budget = list(db.scalars(select(BudgetLine).where(
        BudgetLine.project_id == project.id, BudgetLine.contract_id == contract.id,
        BudgetLine.currency == currency, BudgetLine.budget_period == payload.plan_year,
        BudgetLine.budget_revision == payload.budget_revision, BudgetLine.status != "rejected",
    ).order_by(BudgetLine.id)))
    for item in budget:
        if item.line_kind not in {"legacy_unclassified", "contract_control", "analytical_expense"}:
            conflicts.append({"code": "UNKNOWN_BUDGET_TYPE", "message": f"Строка бюджета {item.id}: неизвестный тип"})
        if item.status not in {"proposed", "approved", "active"}:
            conflicts.append({"code": "BUDGET_STATUS_UNSUPPORTED", "message": f"Строка бюджета {item.id}: неподдерживаемый статус"})
        if item.line_kind == "analytical_expense":
            try:
                if item.source_document_id is None:
                    raise HTTPException(409, "SOURCE_PIN_MISSING")
                assert_document_pin_current(db, project.id, item.source_document_id,
                                            item.source_document_version_id, item.source_document_sha256)
            except HTTPException:
                conflicts.append({"code": "BUDGET_SOURCE_STALE", "message": f"Строка бюджета {item.id}: источник изменился/не закреплён"})
    categories = list(db.scalars(select(CostCategory).where(CostCategory.organization_id == project.organization_id)
                                 .order_by(CostCategory.id)))
    category_by_id = {item.id: item for item in categories}
    article_ids = {article["article_id"] for article in articles}
    if set(payload.category_by_article) - article_ids:
        conflicts.append({"code": "UNKNOWN_ARTICLE", "message": "Категория задана нерасходной/отсутствующей статье"})
    seen_names = set()
    for article in articles:
        name = normalize_article(article["title"])
        article.update(normalized_name=name, budget_line_id=None,
                       cost_category_id=payload.category_by_article.get(article["article_id"]))
        if name in seen_names:
            conflicts.append({"code": "ARTICLE_AMBIGUOUS", "article_id": article["article_id"], "message": article["title"]})
        seen_names.add(name)
        category = category_by_id.get(article["cost_category_id"])
        if article["cost_category_id"] is None:
            conflicts.append({"code": "CATEGORY_CONFIRMATION_REQUIRED", "article_id": article["article_id"], "message": "Выберите категорию явно"})
        elif category is None or not category.is_active:
            conflicts.append({"code": "CATEGORY_UNAVAILABLE", "article_id": article["article_id"], "message": "Категория недоступна в проекте"})
        article["category_name"] = category.name if category else None
        candidates = [item for item in budget if item.line_kind == "analytical_expense"
                      and normalize_article(item.description) == name]
        if len(candidates) > 1:
            conflicts.append({"code": "ARTICLE_AMBIGUOUS", "article_id": article["article_id"], "message": "Несколько строк бюджета с точным именем"})
        elif candidates:
            item = candidates[0]
            article["budget_line_id"] = item.id
            if item.planned_amount != Decimal(article["monthly_total"] or "0") or item.cost_category_id != article["cost_category_id"]:
                conflicts.append({"code": "BUDGET_REVISION_REQUIRED", "article_id": article["article_id"], "message": "Сумма/категория отличается: нужна новая ревизия, не сложение"})
        if len(article["title"]) > 500:
            conflicts.append({"code": "ARTICLE_NAME_TOO_LONG", "article_id": article["article_id"], "message": "Название превышает 500 символов"})
    old_cash = _source_cash(db, payload, document_id)
    for row in old_cash:
        if row.entry_kind not in {"legacy_unclassified", "plan_forecast", "invoice_commitment"}:
            conflicts.append({"code": "UNKNOWN_CASH_TYPE", "message": f"ДДС #{row.id}: неизвестный тип"})
    if old_cash and payload.mode == "import_forecast":
        conflicts.append({"code": "EXISTING_FORECAST_ROWS", "message": "ДДС источника уже загружен: доступен только preview старых связей"})
    existing_rows = []
    for row in old_cash:
        compatible = [article for article in articles if article["normalized_name"] == normalize_article(row.title)]
        match = compatible[0] if len(compatible) == 1 and row.currency == currency and row.planned_date.year == payload.plan_year else None
        source_amount = None
        coordinate = (row.source_name or "").rsplit(", ", 1)[-1]
        if match and row.source_document_version_id == pin.version.id and row.source_document_sha256 == pin.sha256:
            cells = [cell for cell in match["months"] if cell["source_coordinate"] == coordinate]
            if len(cells) == 1:
                source_amount = Decimal(cells[0]["ordinary_amount"])
            else:
                # Legacy source_name may have no sheet, but never match by date alone.
                cells = [cell for cell in match["months"] if cell["source_coordinate"].rsplit("!", 1)[-1] == coordinate]
                if len(cells) == 1:
                    source_amount = Decimal(cells[0]["ordinary_amount"])
        existing_rows.append({
            "id": row.id, "title": row.title, "source_coordinate": coordinate,
            "amount": str(row.planned_amount), "planned_date": row.planned_date.isoformat(),
            "record_version": row.record_version, "status": row.status,
            "contract_id": row.contract_id, "currency": row.currency,
            "source_document_version_id": row.source_document_version_id,
            "source_document_sha256": row.source_document_sha256,
            "current_budget_line_id": row.budget_line_id, "current_schedule_item_id": row.schedule_item_id,
            "proposed_budget_line_id": match["budget_line_id"] if match else None,
            "proposed_article_id": match["article_id"] if match else None,
            "source_difference": str(row.planned_amount - source_amount) if source_amount is not None else None,
            "reason": "EXACT_NORMALIZED_ARTICLE" if match else "ARTICLE_UNRESOLVED",
            "apply_allowed": False,
        })
    with localcontext() as context:
        context.prec = 512
        total = sum((Decimal(article["monthly_total"] or "0") for article in articles), Decimal("0"))
        existing_total = sum((row.planned_amount for row in old_cash), Decimal("0"))
        existing_difference = existing_total - total
    result = {
        **payload.model_dump(), "document_id": document_id, "document_version_id": pin.version.id,
        "document_sha256": pin.sha256, "name": pin.document.name, "currency": currency,
        "algorithm_version": MATRIX_ROUNDING_ALGORITHM, "articles": articles,
        "expense_total": str(total), "warnings": parsed.get("issues", []),
        "existing_expense_total": format(existing_total, ".2f"),
        "existing_expense_difference": format(existing_difference, ".2f") if old_cash else None,
        "conflicts": conflicts, "existing_rows": existing_rows,
        "requires_confirmation": True, "existing_rows_apply_allowed": False,
        "status_after_apply": "proposed", "originals_changed": False,
    }
    stamp = {"result": result, "contract": _snapshot(contract), "budget": [_snapshot(row) for row in budget],
             "cash": [_snapshot(row) for row in old_cash], "categories": [_snapshot(row) for row in categories]}
    result["preview_hash"] = _digest(stamp)
    return result


def preview_article_budget(document_id, payload, db: Session, user):
    require_project_role(db, user, payload.project_id, "viewer")
    with db.no_autoflush:
        return _preview(document_id, payload, db)


def _operation_result(receipt, replayed=False):
    return {**json.loads(receipt.result_json), "operation_id": receipt.id,
            "undone": receipt.undone_at is not None, "replayed": replayed}


def apply_article_budget(document_id, payload, db: Session, user):
    require_project_role(db, user, payload.project_id, "manager")
    if not payload.owner_confirmed:
        raise HTTPException(409, "OWNER_CONFIRMATION_REQUIRED")
    request_hash = _digest({"document_id": document_id, "actor": user.id, "payload": payload.model_dump()})
    # Serialize all imports for this project; re-read after taking the lock.
    db.scalar(select(Project).where(Project.id == payload.project_id).with_for_update().execution_options(populate_existing=True))
    existing = db.scalar(select(DdsArticleBudgetOperation).where(
        DdsArticleBudgetOperation.project_id == payload.project_id,
        DdsArticleBudgetOperation.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
        return _operation_result(existing, True)
    prior = db.scalar(select(DdsArticleBudgetOperation.id).where(
        DdsArticleBudgetOperation.project_id == payload.project_id,
        DdsArticleBudgetOperation.contract_id == payload.contract_id,
        DdsArticleBudgetOperation.source_document_version_id == payload.expected_document_version_id,
        DdsArticleBudgetOperation.mode == payload.mode,
        DdsArticleBudgetOperation.budget_period == payload.plan_year,
        DdsArticleBudgetOperation.budget_revision == payload.budget_revision))
    if prior:
        raise HTTPException(409, "SOURCE_ALREADY_APPLIED")
    # Hold source, categories and existing budget rows through commit.  Preview itself takes no locks/writes.
    for model, predicate in (
        (Contract, Contract.id == payload.contract_id), (Document, Document.id == document_id),
        (DocumentVersion, DocumentVersion.id == payload.expected_document_version_id),
        (CostCategory, CostCategory.id.in_(list(payload.category_by_article.values()))),
        (CashFlowEntry, (CashFlowEntry.project_id == payload.project_id) & (CashFlowEntry.source_document_id == document_id)),
        (BudgetLine, (BudgetLine.project_id == payload.project_id) & (BudgetLine.contract_id == payload.contract_id)),
    ):
        list(db.scalars(select(model).where(predicate).order_by(model.id).with_for_update().execution_options(populate_existing=True)))
    resolve_current_document_pin(db, payload.project_id, document_id,
                                 payload.expected_document_version_id, payload.expected_document_sha256)
    preview_payload = ArticleBudgetPreviewRequest(**{key: getattr(payload, key) for key in ArticleBudgetPreviewRequest.model_fields})
    proposal = _preview(document_id, preview_payload, db)
    if proposal["preview_hash"] != payload.preview_hash:
        raise HTTPException(409, "PREVIEW_STALE: повторите preview, данные изменились")
    if proposal["conflicts"]:
        raise HTTPException(409, {"code": "PREVIEW_CONFLICTS", "conflicts": proposal["conflicts"]})
    try:
        with db.begin_nested():
            receipt = DdsArticleBudgetOperation(
                project_id=payload.project_id, contract_id=payload.contract_id, actor_user_id=user.id,
                source_document_id=document_id, source_document_version_id=payload.expected_document_version_id,
                source_document_sha256=payload.expected_document_sha256, mode=payload.mode,
                budget_period=payload.plan_year, budget_revision=payload.budget_revision,
                idempotency_key=payload.idempotency_key, request_hash=request_hash, preview_hash=payload.preview_hash,
                algorithm_version=MATRIX_ROUNDING_ALGORITHM, result_json="{}", snapshot_json="{}")
            db.add(receipt); db.flush()
            created_budget, used_budget, created_cash = [], [], []
            for article in proposal["articles"]:
                line = db.get(BudgetLine, article["budget_line_id"]) if article["budget_line_id"] else None
                if line:
                    used_budget.append(line)
                else:
                    line = BudgetLine(
                        project_id=payload.project_id, contract_id=payload.contract_id,
                        line_kind="analytical_expense", budget_period=payload.plan_year,
                        budget_revision=payload.budget_revision, article_normalized_name=article["normalized_name"],
                        cost_category_id=article["cost_category_id"], category=article["category_name"],
                        description=article["title"], planned_amount=Decimal(article["monthly_total"]),
                        forecast_amount=Decimal(article["monthly_total"]), currency=proposal["currency"], status="proposed",
                        source_document_id=document_id, source_document_version_id=payload.expected_document_version_id,
                        source_document_sha256=payload.expected_document_sha256,
                        source_name=f"{proposal['name']}, {article['source_coordinate']}",
                        source_excerpt=_json({"algorithm": MATRIX_ROUNDING_ALGORITHM, "article": article}))
                    db.add(line); db.flush(); created_budget.append(line)
                if payload.mode == "import_forecast":
                    for month in article["months"]:
                        if Decimal(month["amount"]) == 0:
                            continue
                        from calendar import monthrange
                        row = CashFlowEntry(
                            project_id=payload.project_id, contract_id=payload.contract_id, budget_line_id=line.id,
                            entry_kind="plan_forecast", matrix_operation_id=receipt.id,
                            matrix_article_id=article["article_id"], matrix_month=month["month"],
                            source_document_id=document_id, source_document_version_id=payload.expected_document_version_id,
                            source_document_sha256=payload.expected_document_sha256, direction="outflow",
                            title=article["title"], planned_amount=Decimal(month["amount"]),
                            planned_date=date(payload.plan_year, month["month"], monthrange(payload.plan_year, month["month"])[1]),
                            currency=proposal["currency"], status="proposed", cost_category_id=article["cost_category_id"],
                            category=article["category_name"], note=article["title"],
                            source_name=f"{proposal['name']}, {month['source_coordinate']}", source_excerpt=_json(month))
                        db.add(row); db.flush(); created_cash.append(row)
            receipt.result_json = _json({"created_budget_ids": [row.id for row in created_budget],
                                         "used_budget_ids": [row.id for row in used_budget],
                                         "created_cash_flow_ids": [row.id for row in created_cash],
                                         "expense_total": proposal["expense_total"], "status": "proposed",
                                         "existing_rows_changed": 0})
            receipt.snapshot_json = _json({"budget": [_snapshot(row) for row in created_budget],
                                           "cash": [_snapshot(row) for row in created_cash]})
            db.add(AuditLog(action="dds_article_budget_applied", entity_type="dds_article_budget_operation", entity_id=receipt.id,
                            details=_json({"user": user.id, "preview_hash": payload.preview_hash,
                                           "owner_confirmed": True, "result": json.loads(receipt.result_json)})))
        db.commit()
        return _operation_result(receipt)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "CONCURRENT_IMPORT_CONFLICT: повторите preview") from exc


def undo_article_budget(operation_id, db: Session, user):
    receipt = db.get(DdsArticleBudgetOperation, operation_id)
    if receipt is None:
        raise HTTPException(404, "Операция не найдена")
    require_project_role(db, user, receipt.project_id, "manager")
    db.scalar(select(Project).where(Project.id == receipt.project_id).with_for_update())
    receipt = db.scalar(select(DdsArticleBudgetOperation).where(DdsArticleBudgetOperation.id == operation_id)
                        .with_for_update().execution_options(populate_existing=True))
    if receipt.undone_at is not None:
        return _operation_result(receipt, True)
    assert_document_pin_current(db, receipt.project_id, receipt.source_document_id,
                                receipt.source_document_version_id, receipt.source_document_sha256)
    snapshots = json.loads(receipt.snapshot_json)
    owned_cash = {snapshot["id"] for snapshot in snapshots["cash"]}
    owned_budget = {snapshot["id"] for snapshot in snapshots["budget"]}
    rows_to_cancel = []
    rows_to_reject = []
    for model, key, target in ((CashFlowEntry, "cash", rows_to_cancel), (BudgetLine, "budget", rows_to_reject)):
        for snapshot in snapshots[key]:
            row = db.scalar(select(model).where(model.id == snapshot["id"]).with_for_update().execution_options(populate_existing=True))
            if row is None or _snapshot(row) != snapshot:
                raise HTTPException(409, "UNDO_DEPENDENCY_CONFLICT: запись изменена после применения")
            target.append(row)
    dependent_cash = db.scalar(select(CashFlowEntry.id).where(CashFlowEntry.budget_line_id.in_(owned_budget),
                                                              CashFlowEntry.id.not_in(owned_cash)))
    dependent_act = db.scalar(select(AcceptanceAct.id).where(AcceptanceAct.budget_line_id.in_(owned_budget)))
    dependent_payment = db.scalar(select(PaymentEvent.id).where(PaymentEvent.cash_flow_entry_id.in_(owned_cash)))
    other_receipts = list(db.scalars(select(DdsArticleBudgetOperation).where(
        DdsArticleBudgetOperation.project_id == receipt.project_id,
        DdsArticleBudgetOperation.id != receipt.id, DdsArticleBudgetOperation.undone_at.is_(None))))
    dependent_receipt = any(owned_budget.intersection(json.loads(other.result_json).get("used_budget_ids", []))
                            for other in other_receipts)
    if dependent_cash or dependent_act or dependent_payment or dependent_receipt:
        raise HTTPException(409, "UNDO_DEPENDENCY_CONFLICT: появились зависимые ДДС/акты/платежи")
    for row in rows_to_cancel:
        row.status = "cancelled"; row.record_version += 1
    for row in rows_to_reject:
        row.status = "rejected"; row.record_version += 1
    receipt.undone_at = datetime.now(timezone.utc)
    db.add(AuditLog(action="dds_article_budget_undone", entity_type="dds_article_budget_operation", entity_id=receipt.id,
                    details=_json({"user": user.id, "budget_ids": sorted(owned_budget), "cash_ids": sorted(owned_cash)})))
    db.commit()
    return _operation_result(receipt)


def confirmed_budget_for_totals(rows, known_control_ids=()):
    """Keep the latest confirmed analytical revision and do not add its control total."""
    confirmed = [row for row in rows if row.status in {"approved", "active", "closed"}]
    latest = {}
    counts = {}
    for row in confirmed:
        if row.line_kind == "analytical_expense":
            if row.budget_period is None or row.budget_revision is None:
                raise HTTPException(409, "BUDGET_SCOPE_UNKNOWN")
            key = (row.contract_id, row.currency, row.budget_period, normalize_article(row.description))
            latest[key] = max(latest.get(key, 0), row.budget_revision)
            counts[(key, row.budget_revision)] = counts.get((key, row.budget_revision), 0) + 1
        elif row.line_kind not in {"legacy_unclassified", "contract_control"}:
            raise HTTPException(409, "UNKNOWN_BUDGET_TYPE")
    if any(counts[(key, revision)] > 1 for key, revision in latest.items()):
        raise HTTPException(409, "ARTICLE_AMBIGUOUS: несколько подтверждённых статей одной ревизии")
    analytical_contracts = {(key[0], key[1]) for key in latest}
    if any(row.line_kind == "legacy_unclassified" and row.id not in known_control_ids
           and (row.contract_id, row.currency) in analytical_contracts for row in confirmed):
        # Old rows have no reliable type/period. Do not guess whether they are
        # control totals or expenses, and do not silently double the budget.
        raise HTTPException(409, "BUDGET_SCOPE_UNKNOWN: определите тип старых бюджетных строк")
    return [row for row in confirmed if
            (row.line_kind == "analytical_expense" and row.budget_revision == latest[(row.contract_id, row.currency, row.budget_period, normalize_article(row.description))])
            or (row.line_kind == "legacy_unclassified" and row.id not in known_control_ids)
            or ((row.line_kind == "contract_control" or row.id in known_control_ids)
                and (row.contract_id, row.currency) not in analytical_contracts)]
