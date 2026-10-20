"""Generation of stage acceptance acts and signing inflow cash-flow entries."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acceptance_act_document import (
    ActData,
    ActParty,
    rubles_in_words,  # noqa: F401  (re-exported for callers)
    stage_quantity,
    vat_from_gross,
)
from app.contract_stages import contract_text
from app.models.audit_log import AuditLog
from app.models.contract_stage import ContractStage
from app.models.execution_finance import AcceptanceAct, CashFlowEntry
from app.models.organization_contract import Organization
from app.models.project import Project


_ACT_NUMBER_RE = re.compile(r"\d+")
_OKPD2_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{2}\.\d{3}\b")
_PAYMENT_RE = re.compile(
    r"в\s+течени[еи]\s+(\d+)\s*(?:\([^)]*\))?\s*(рабочих|календарных)\s+дн",
    re.IGNORECASE,
)
_CITY_RE = re.compile(r"г\.\s*([А-ЯЁ][а-яё\-]+)")


def next_act_number(db: Session, contract_id: int) -> str:
    acts = db.query(AcceptanceAct).filter(AcceptanceAct.contract_id == contract_id).all()
    maximum = 0
    for act in acts:
        numbers = re.findall(r"\d+", act.number or "")
        if not numbers:
            continue
        try:
            value = int(numbers[0])
        except ValueError:
            continue
        if value > maximum:
            maximum = value
    return str(maximum + 1)


def object_total(db: Session, stage: ContractStage) -> Decimal:
    stmt = select(ContractStage.amount).where(
        ContractStage.contract_id == stage.contract_id,
    )
    if stage.object_name is None:
        stmt = stmt.where(ContractStage.object_name.is_(None))
    else:
        stmt = stmt.where(ContractStage.object_name == stage.object_name)
    total = Decimal("0.00")
    for (amount,) in db.execute(stmt).all():
        if amount is not None:
            total += Decimal(amount)
    return total


def contract_okpd2(text: str) -> str | None:
    if not text:
        return None
    match = _OKPD2_RE.search(text)
    return match.group(0) if match else None


def payment_days(text: str) -> tuple[int, bool]:
    if not text:
        return (10, True)
    for match in _PAYMENT_RE.finditer(text):
        window = text[max(0, match.start() - 300): match.end() + 300].lower()
        if "оплат" in window:
            try:
                days = int(match.group(1))
            except ValueError:
                continue
            return (days, match.group(2).lower().startswith("рабоч"))
    return (10, True)


def add_working_days(start: date, days: int) -> date:
    current = start
    remaining = max(days, 0)
    while remaining > 0:
        current = current + timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _city_from_address(address: str | None) -> str | None:
    if not address:
        return None
    match = _CITY_RE.search(address)
    return match.group(1) if match else None


def _okpd2_from_contract(db: Session, project_id: int, contract) -> str | None:
    texts = contract_text(db, project_id, contract)
    joined = "\n".join(text for _, _, text in texts)
    return contract_okpd2(joined)


def _payment_terms(db: Session, project_id: int, contract) -> tuple[int, bool]:
    texts = contract_text(db, project_id, contract)
    joined = "\n".join(text for _, _, text in texts)
    return payment_days(joined)


def build_act_data(db: Session, project_id: int, contract, stage: ContractStage, act_date: date | None) -> ActData:
    project = db.get(Project, project_id)
    our_org = db.get(Organization, project.organization_id) if project else None
    counterparty_org = (
        db.get(Organization, contract.counterparty_organization_id)
        if getattr(contract, "counterparty_organization_id", None) else None
    )

    our_name = our_org.name if our_org else ""
    counterparty_org_name = counterparty_org.name if counterparty_org else ""
    counterparty_fallback = (getattr(contract, "counterparty", None) or "").strip()
    counterparty_name = counterparty_org_name or counterparty_fallback

    if getattr(contract, "contract_kind", None) in ("customer", "prime_reference"):
        customer_name = counterparty_name
        contractor_name = our_name
    else:
        customer_name = our_name
        contractor_name = counterparty_name

    our_signer_name = our_org.director_name if (our_org and our_org.director_name) else "____________"
    counterparty_signer_name = (
        counterparty_org.director_name if (counterparty_org and counterparty_org.director_name) else ""
    )

    def _position(signer_name: str) -> str:
        return "генерального директора" if signer_name else ""

    our_signer_position = _position(our_signer_name)
    counterparty_signer_position = _position(counterparty_signer_name)

    if getattr(contract, "contract_kind", None) in ("customer", "prime_reference"):
        customer_name = counterparty_name
        contractor_name = our_name
        customer_signer_position = counterparty_signer_position
        customer_signer_name = counterparty_signer_name
        contractor_signer_position = our_signer_position
        contractor_signer_name = our_signer_name
    else:
        customer_name = our_name
        contractor_name = counterparty_name
        customer_signer_position = our_signer_position
        customer_signer_name = our_signer_name
        contractor_signer_position = counterparty_signer_position
        contractor_signer_name = counterparty_signer_name

    customer = ActParty(
        name=customer_name,
        role="customer",
        signer_position=customer_signer_position,
        signer_name=customer_signer_name,
    )
    contractor = ActParty(
        name=contractor_name,
        role="contractor",
        signer_position=contractor_signer_position,
        signer_name=contractor_signer_name,
    )

    vat_rate = None
    if getattr(contract, "vat_mode", None) == "rate":
        vat_rate = getattr(contract, "vat_rate", None)

    total = object_total(db, stage)
    if total != stage.amount:
        quantity = stage_quantity(stage.amount, total)
        unit = "усл. ед."
        unit_price = total
    else:
        quantity = Decimal("1")
        unit = "этап"
        unit_price = stage.amount

    okpd2 = _okpd2_from_contract(db, project_id, contract)
    city = _city_from_address(our_org.legal_address if our_org else None)

    return ActData(
        number="",
        act_date=act_date,
        city=city,
        contract_number=contract.number or "",
        contract_date=getattr(contract, "date", None),
        customer=customer,
        contractor=contractor,
        object_name=stage.object_name,
        stage_number=stage.stage_number,
        stage_title=stage.title or "",
        okpd2=okpd2,
        unit=unit,
        quantity=quantity,
        unit_price=unit_price,
        amount=stage.amount,
        vat_rate=vat_rate,
    )


def create_stage_act(
    db: Session,
    project_id: int,
    contract,
    stage: ContractStage,
    user,
    number: str | None = None,
) -> AcceptanceAct:
    existing = db.query(AcceptanceAct).filter(
        AcceptanceAct.stage_id == stage.id,
        AcceptanceAct.status != "cancelled",
    ).first()
    if existing is not None:
        return existing

    if number is not None:
        normalized = number.strip()
        if normalized:
            conflict = db.query(AcceptanceAct).filter(
                AcceptanceAct.contract_id == contract.id,
                AcceptanceAct.number == normalized,
                AcceptanceAct.status != "cancelled",
            ).first()
            if conflict is not None:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=409,
                    detail=f"Акт № {normalized} по этому договору уже есть",
                )
            number = normalized
        else:
            number = None

    total = object_total(db, stage)
    quantity = stage_quantity(stage.amount, total)
    vat_rate = None
    if getattr(contract, "vat_mode", None) == "rate":
        vat_rate = getattr(contract, "vat_rate", None)
    vat_amount = vat_from_gross(stage.amount, vat_rate) if vat_rate is not None else None

    title = f"{stage.stage_number}-й этап"
    if stage.object_name:
        title = f"{title} ({stage.object_name})"

    act = AcceptanceAct(
        project_id=project_id,
        contract_id=contract.id,
        stage_id=stage.id,
        number=number if number is not None else next_act_number(db, contract.id),
        title=title,
        act_date=None,
        amount=stage.amount,
        quantity=quantity,
        vat_amount=vat_amount,
        status="draft",
        generated=True,
        source_name="contract_stage",
    )
    db.add(act)
    db.flush()
    db.add(AuditLog(
        action="acceptance_act_generated",
        entity_type="acceptance_act",
        entity_id=act.id,
        details=f"contract_id={contract.id}; stage_id={stage.id}",
    ))
    db.flush()
    return act


def mark_act_signed(db: Session, act: AcceptanceAct, act_date: date, user) -> AcceptanceAct:
    act.status = "signed"
    act.act_date = act_date

    if act.cash_flow_entry_id is None:
        project = db.get(Project, act.project_id)
        contract = None
        if act.contract_id is not None and project is not None:
            from app.models.organization_contract import Contract as _Contract
            contract = db.get(_Contract, act.contract_id)
        if contract is not None:
            days, working = _payment_terms(db, act.project_id, contract)
        else:
            days, working = (10, True)
        planned_date = add_working_days(act_date, days) if working else act_date + timedelta(days=days)

        entry = CashFlowEntry(
            project_id=act.project_id,
            contract_id=act.contract_id,
            direction="inflow",
            title=f"Оплата по акту № {act.number}: {act.title}",
            planned_date=planned_date,
            planned_amount=act.amount,
            actual_amount=Decimal("0"),
            currency="RUB",
            status="approved",
            source_name=f"acceptance_act:{act.id}",
        )
        db.add(entry)
        db.flush()
        act.cash_flow_entry_id = entry.id

    db.add(AuditLog(
        action="acceptance_act_signed",
        entity_type="acceptance_act",
        entity_id=act.id,
        details=f"cash_flow_entry_id={act.cash_flow_entry_id}",
    ))
    db.flush()
    return act
