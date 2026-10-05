"""Owner-confirmed existing DDS budget links, separate from import and approval."""
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_project_role
from app.dds_article_budget import _digest, _json, _snapshot, _snapshot_matches, normalize_article
from app.finance_money import project_currency
from app.finance_source_pins import assert_document_pin_current, resolve_current_document_pin
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory, DdsBudgetLinkOperation
from app.models.organization_contract import Contract
from app.models.project import Project
from app.structured_import import parse_structured_rows

MAX_LINK_ROWS = 1000


class BudgetLinkPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)
    contract_id: int = Field(gt=0)
    plan_year: int = Field(ge=2000, le=2100)
    budget_revision: int = Field(ge=1, le=10000)


class BudgetLinkApplyRequest(BudgetLinkPreviewRequest):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_document_version_id: int = Field(gt=0)
    expected_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_cash_versions: dict[int, int] = Field(min_length=1, max_length=MAX_LINK_ROWS)
    idempotency_key: str = Field(min_length=8, max_length=128)
    owner_confirmed: bool = False


def _scope(db, document_id, payload):
    project = db.get(Project, payload.project_id)
    contract = db.scalar(select(Contract).where(Contract.id == payload.contract_id, Contract.project_id == payload.project_id))
    document = db.scalar(select(Document).where(Document.id == document_id, Document.project_id == payload.project_id))
    if project is None or contract is None or document is None or contract.contract_kind == "prime_reference":
        raise HTTPException(422, "LINK_SCOPE_MISMATCH")
    return project, contract, document


def _cash_query(document_id, payload):
    return select(CashFlowEntry).where(CashFlowEntry.project_id == payload.project_id,
        CashFlowEntry.contract_id == payload.contract_id, CashFlowEntry.source_document_id == document_id,
        CashFlowEntry.direction == "outflow").order_by(CashFlowEntry.id).limit(MAX_LINK_ROWS + 1)


def _budget_query(payload, currency):
    return select(BudgetLine).where(BudgetLine.project_id == payload.project_id,
        BudgetLine.contract_id == payload.contract_id, BudgetLine.currency == currency,
        BudgetLine.budget_period == payload.plan_year, BudgetLine.budget_revision == payload.budget_revision,
        BudgetLine.line_kind == "analytical_expense").order_by(BudgetLine.id)


def _preview(document_id, payload, db):
    project, contract, document = _scope(db, document_id, payload)
    try:
        currency = project_currency(db, project.id)
    except ValueError as exc:
        raise HTTPException(422, f"PROJECT_CURRENCY_INVALID: {exc}") from exc
    pin = resolve_current_document_pin(db, project.id, document_id)
    parsed = parse_structured_rows(pin.version.content or "", "cash-flow", source_name=document.name, plan_year=payload.plan_year)
    conflicts = [{"code":"MATRIX_INVALID", "message":message} for message in parsed.get("blocking_issues", [])]
    if parsed.get("layout") != "monthly_matrix":
        conflicts.append({"code":"MATRIX_REQUIRED", "message":"Источник должен быть матрицей статья × месяц"})
    articles = [a for a in parsed.get("articles", []) if a["direction"] == "outflow"]
    categories = list(db.scalars(select(CostCategory).where(CostCategory.organization_id == project.organization_id).order_by(CostCategory.id)))
    category_by_id = {c.id:c for c in categories}
    budgets = list(db.scalars(_budget_query(payload, currency)))
    old_cash = list(db.scalars(_cash_query(document_id, payload)))
    if len(old_cash) > MAX_LINK_ROWS:
        raise HTTPException(422, "LINK_BATCH_TOO_LARGE")
    cash = [row for row in old_cash if row.status != "cancelled"]
    if not cash:
        conflicts.append({"code":"NO_LINK_CANDIDATES", "message":"Нет существующих расходов для связывания"})
    source_pins = []
    for line in budgets:
        try:
            if line.source_document_id is None:
                raise HTTPException(409, "SOURCE_PIN_MISSING")
            budget_pin = assert_document_pin_current(db, project.id, line.source_document_id,
                line.source_document_version_id, line.source_document_sha256)
            source_pins.append({"document":_snapshot(budget_pin.document), "version":_snapshot(budget_pin.version), "sha256":budget_pin.sha256})
        except HTTPException:
            conflicts.append({"code":"BUDGET_SOURCE_STALE", "budget_line_id":line.id, "message":"Источник бюджета изменён или не закреплён"})
        if line.status not in {"proposed", "approved", "active"}:
            conflicts.append({"code":"BUDGET_STATUS_UNSUPPORTED", "budget_line_id":line.id, "message":"Бюджет закрыт или отклонён"})
        category = category_by_id.get(line.cost_category_id)
        if category is None or not category.is_active:
            conflicts.append({"code":"CATEGORY_UNAVAILABLE", "budget_line_id":line.id, "message":"Категория бюджета недоступна"})
    rows, used = [], {}
    for row in cash:
        def conflict(code, message):
            conflicts.append({"code":code,"cash_flow_id":row.id,"message":message})
        name = normalize_article(row.title)
        source_matches = [a for a in articles if normalize_article(a["title"]) == name]
        matches = [line for line in budgets if normalize_article(line.description) == name]
        article = source_matches[0] if len(source_matches) == 1 else None
        line = matches[0] if len(matches) == 1 else None
        if article is None:
            conflict("SOURCE_ARTICLE_UNRESOLVED", "Статья не определена однозначно в матричном источнике")
        if len(matches) > 1:
            conflict("ARTICLE_AMBIGUOUS", "Несколько бюджетных строк с точным нормализованным названием")
        elif not matches:
            conflict("BUDGET_LINE_REQUIRED", "Нет аналитической бюджетной строки в выбранном контуре")
        if row.currency != currency or row.planned_date.year != payload.plan_year:
            conflict("CASH_SCOPE_MISMATCH", "Валюта или период расхода отличается")
        if row.status != "proposed" or row.actual_date is not None or row.actual_amount != 0:
            conflict("LINK_REQUIRES_UNCONFIRMED_PLAN", "Связывание доступно только для неподтверждённого плана без факта")
        if row.entry_kind not in {"legacy_unclassified", "plan_forecast"}:
            conflict("CASH_TYPE_UNSUPPORTED", "Счета и обязательства не используют этот путь связывания")
        if row.budget_line_id is not None:
            conflict("CASH_ALREADY_LINKED", "Расход уже связан: сначала отмените прежнюю операцию")
        if row.source_document_version_id != pin.version.id or row.source_document_sha256 != pin.sha256:
            conflict("CASH_SOURCE_STALE", "Расход не закреплён за текущей версией источника")
        coordinate = (row.source_name or "").rsplit(", ", 1)[-1]
        cells = [] if article is None else [cell for cell in article["months"] if cell["source_coordinate"] == coordinate]
        if not cells and article is not None and "!" not in coordinate:
            cells = [cell for cell in article["months"] if cell["source_coordinate"].rsplit("!", 1)[-1] == coordinate]
        if len(cells) != 1:
            conflict("CASH_SOURCE_CELL_MISSING", "Исходная месячная ячейка расхода не доказана")
        if line is not None:
            used[line.id] = line
        rows.append({"id":row.id,"title":row.title,"amount":format(row.planned_amount,".2f"),
            "planned_date":row.planned_date.isoformat(),"status":row.status,"currency":row.currency,
            "record_version":row.record_version,"source_coordinate":coordinate,
            "current_budget_line_id":row.budget_line_id,"budget_line_id":line.id if line else None,
            "budget_title":line.description if line else None,"budget_record_version":line.record_version if line else None,
            "budget_amount":format(line.planned_amount,".2f") if line else None,
            "category":line.category if line else None})
    with localcontext() as context:
        context.prec = 512
        expense_total = sum((r.planned_amount for r in cash), Decimal(0))
        budget_total = sum((r.planned_amount for r in used.values()), Decimal(0))
        difference = expense_total - budget_total
    result = {**payload.model_dump(),"document_id":document_id,"document_version_id":pin.version.id,
        "document_sha256":pin.sha256,"currency":currency,"rows":rows,"conflicts":conflicts,
        "warnings":parsed.get("issues",[]),"excluded_cancelled_ids":[row.id for row in old_cash if row.status=="cancelled"],
        "expense_total":format(expense_total,".2f"),"budget_total":format(budget_total,".2f"),
        "difference":format(difference,".2f"),"apply_allowed":bool(rows) and not conflicts,
        "only_changed_business_field":"budget_line_id","requires_confirmation":True}
    result["preview_hash"] = _digest({"result":result,"contract":_snapshot(contract),"document":_snapshot(document),
        "version":_snapshot(pin.version),"cash":[_snapshot(r) for r in old_cash],"budget":[_snapshot(r) for r in budgets],
        "categories":[_snapshot(r) for r in categories],"budget_source_pins":source_pins})
    return result


def preview_budget_links(document_id, payload, db: Session, user):
    require_project_role(db,user,payload.project_id,"viewer")
    with db.no_autoflush:
        return _preview(document_id,payload,db)


def _result(receipt, replayed=False):
    result = json.loads(receipt.result_json)
    return {**result,"operation_id":receipt.id,"undone":receipt.undone_at is not None,
        "active_link_count":0 if receipt.undone_at is not None else result["link_count"],"replayed":replayed,
        "created_at":str(receipt.created_at),"actor_user_id":receipt.actor_user_id}


def list_budget_link_operations(document_id, payload, db: Session, user):
    require_project_role(db,user,payload.project_id,"viewer")
    _scope(db,document_id,payload)
    return [_result(row) for row in db.scalars(select(DdsBudgetLinkOperation).where(
        DdsBudgetLinkOperation.project_id==payload.project_id,DdsBudgetLinkOperation.contract_id==payload.contract_id,
        DdsBudgetLinkOperation.source_document_id==document_id,DdsBudgetLinkOperation.budget_period==payload.plan_year,
        DdsBudgetLinkOperation.budget_revision==payload.budget_revision).order_by(DdsBudgetLinkOperation.id.desc()).limit(50))]


def apply_budget_links(document_id, payload, db: Session, user):
    require_project_role(db,user,payload.project_id,"manager")
    if not payload.owner_confirmed:
        raise HTTPException(409,"OWNER_CONFIRMATION_REQUIRED")
    _scope(db,document_id,payload)
    request_hash = _digest({"document_id":document_id,"actor":user.id,"payload":payload.model_dump()})
    db.scalar(select(Project).where(Project.id==payload.project_id).with_for_update().execution_options(populate_existing=True))
    existing = db.scalar(select(DdsBudgetLinkOperation).where(DdsBudgetLinkOperation.project_id==payload.project_id,
        DdsBudgetLinkOperation.idempotency_key==payload.idempotency_key))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(409,"IDEMPOTENCY_CONFLICT")
        return _result(existing,True)
    try:
        currency = project_currency(db,payload.project_id)
    except ValueError as exc:
        raise HTTPException(422,f"PROJECT_CURRENCY_INVALID: {exc}") from exc
    budgets = list(db.scalars(_budget_query(payload,currency).with_for_update().execution_options(populate_existing=True)))
    cash = list(db.scalars(_cash_query(document_id,payload).with_for_update().execution_options(populate_existing=True)))
    doc_ids = {document_id} | {line.source_document_id for line in budgets if line.source_document_id is not None}
    version_ids = {payload.expected_document_version_id} | {line.source_document_version_id for line in budgets if line.source_document_version_id is not None}
    for model, predicate in ((Contract,Contract.id==payload.contract_id),
        (Document,Document.id.in_(doc_ids)),(DocumentVersion,DocumentVersion.id.in_(version_ids)),
        (CostCategory,CostCategory.id.in_([line.cost_category_id for line in budgets if line.cost_category_id is not None]))):
        list(db.scalars(select(model).where(predicate).order_by(model.id).with_for_update().execution_options(populate_existing=True)))
    resolve_current_document_pin(db,payload.project_id,document_id,payload.expected_document_version_id,payload.expected_document_sha256)
    current = {row.id:row.record_version for row in cash if row.status != "cancelled"}
    if current != payload.expected_cash_versions:
        raise HTTPException(409,"CASH_FLOW_VERSION_MISMATCH: состав или версия расхода изменились после preview")
    preview_payload = BudgetLinkPreviewRequest(**{key:getattr(payload,key) for key in BudgetLinkPreviewRequest.model_fields})
    proposal = _preview(document_id,preview_payload,db)
    if proposal["preview_hash"] != payload.preview_hash:
        raise HTTPException(409,"LINK_PREVIEW_STALE: повторите preview, данные изменились")
    if not proposal["apply_allowed"]:
        raise HTTPException(409,{"code":"LINK_PREVIEW_CONFLICTS","conflicts":proposal["conflicts"]})
    targets = {row["id"]:row["budget_line_id"] for row in proposal["rows"]}
    cash_by_id = {row.id:row for row in cash}
    try:
        with db.begin_nested():
            receipt = DdsBudgetLinkOperation(project_id=payload.project_id,contract_id=payload.contract_id,
                actor_user_id=user.id,source_document_id=document_id,source_document_version_id=payload.expected_document_version_id,
                source_document_sha256=payload.expected_document_sha256,currency=currency,budget_period=payload.plan_year,
                budget_revision=payload.budget_revision,idempotency_key=payload.idempotency_key,request_hash=request_hash,
                preview_hash=payload.preview_hash,result_json="{}",snapshot_json="{}")
            db.add(receipt); db.flush()
            snapshots = []
            for item_id,budget_id in targets.items():
                row = cash_by_id[item_id]
                before = _snapshot(row)
                row.budget_line_id = budget_id
                row.record_version += 1
                db.flush()
                snapshots.append({"before":before,"after":_snapshot(row)})
                db.add(AuditLog(action="cash_flow_budget_link_applied",entity_type="cash_flow",entity_id=row.id,
                    details=_json({"user":user.id,"operation_id":receipt.id,"before_budget_line_id":None,
                        "after_budget_line_id":budget_id,"before_version":before["record_version"],"after_version":row.record_version,
                        "amount":format(row.planned_amount,".2f"),"planned_date":row.planned_date.isoformat(),
                        "status":row.status,"owner_confirmed":True,"preview_hash":payload.preview_hash})))
            receipt.snapshot_json = _json(snapshots)
            receipt.result_json = _json({"link_count":len(targets),"linked_cash_flow_ids":list(targets),
                "links":[{"cash_flow_id":i,"budget_line_id":b} for i,b in targets.items()],
                "expense_total":proposal["expense_total"],"changed_business_fields":["budget_line_id"]})
        db.commit()
        return _result(receipt)
    except IntegrityError as exc:
        db.rollback()
        replay = db.scalar(select(DdsBudgetLinkOperation).where(DdsBudgetLinkOperation.project_id==payload.project_id,
            DdsBudgetLinkOperation.idempotency_key==payload.idempotency_key))
        if replay is not None and replay.request_hash == request_hash:
            return _result(replay,True)
        raise HTTPException(409,"CONCURRENT_LINK_OPERATION: повторите preview") from exc


def undo_budget_links(operation_id, db: Session, user):
    receipt = db.get(DdsBudgetLinkOperation,operation_id)
    if receipt is None:
        raise HTTPException(404,"Операция связей не найдена")
    require_project_role(db,user,receipt.project_id,"manager")
    db.scalar(select(Project).where(Project.id==receipt.project_id).with_for_update())
    receipt = db.scalar(select(DdsBudgetLinkOperation).where(DdsBudgetLinkOperation.id==operation_id)
        .with_for_update().execution_options(populate_existing=True))
    if receipt.undone_at is not None:
        return _result(receipt,True)
    snapshots = json.loads(receipt.snapshot_json)
    ids = [item["after"]["id"] for item in snapshots]
    if len(ids) != len(set(ids)) or not ids:
        raise HTTPException(409,"LINK_RECEIPT_INVALID")
    rows = {row.id:row for row in db.scalars(select(CashFlowEntry).where(CashFlowEntry.id.in_(ids))
        .order_by(CashFlowEntry.id).with_for_update().execution_options(populate_existing=True))}
    # Check the whole batch before touching any link. Source replacements alone
    # do not prevent undo of unchanged historical rows; the saved row CAS does.
    for item in snapshots:
        row = rows.get(item["after"]["id"])
        if row is None or row.project_id != receipt.project_id or row.contract_id != receipt.contract_id or not _snapshot_matches(row, item["after"]):
            raise HTTPException(409,"LINK_UNDO_STALE: после операции расход изменён; ни одна связь не отменена")
        if item["before"]["budget_line_id"] is not None:
            raise HTTPException(409,"LINK_RECEIPT_INVALID")
    with db.begin_nested():
        for item in snapshots:
            row = rows[item["after"]["id"]]
            old_link, old_version = row.budget_line_id, row.record_version
            row.budget_line_id = None
            row.record_version += 1
            db.add(AuditLog(action="cash_flow_budget_link_undone",entity_type="cash_flow",entity_id=row.id,
                details=_json({"user":user.id,"operation_id":receipt.id,"before_budget_line_id":old_link,
                    "after_budget_line_id":None,"before_version":old_version,"after_version":row.record_version,
                    "amount":format(row.planned_amount,".2f"),"planned_date":row.planned_date.isoformat(),"status":row.status})))
        receipt.undone_at = datetime.now(timezone.utc)
        db.flush()
    db.commit()
    return _result(receipt)
