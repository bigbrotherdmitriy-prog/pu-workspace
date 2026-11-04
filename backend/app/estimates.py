"""Сметы и акты КС-2/КС-3: объёмы берутся только из журнала работ (факт), цены из сметы."""
from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.estimates import Estimate, EstimateLine, KsAct
from app.models.project import Project
from app.models.work_log import WorkLogEntry

TWO = Decimal("0.01")
THREE = Decimal("0.001")
ZERO = Decimal("0")


def _dec(value) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None


def _validate_storable(value: Decimal | None, scale: int) -> None:
    if value is None:
        return
    if not value.is_finite():
        raise ValueError("Число должно быть конечным")
    bound = Decimal(10) ** (18 - scale)
    if value.copy_abs() >= bound:
        raise ValueError("Число превышает допустимый диапазон поля")
    # Match the existing service serialization policy before checking DB fit.
    from decimal import localcontext
    with localcontext() as context:
        context.prec = 28
        rounded = value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    if abs(rounded) >= bound:
        raise ValueError("Число превышает допустимый диапазон поля")



def _money(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def _s(value: Decimal | None, q: Decimal = TWO) -> str | None:
    return f"{value.quantize(q, rounding=ROUND_HALF_UP):f}" if value is not None else None


def _estimate_dict(e: Estimate, lines_count: int = 0) -> dict:
    return {
        "id": e.id, "name": e.name, "contract_id": e.contract_id, "source_name": e.source_name,
        "total": _s(e.total), "vat_rate": _s(e.vat_rate), "status": e.status, "lines_count": lines_count,
    }


def create_estimate(db: Session, project_id: int, name: str, parsed: dict, contract_id: int | None = None,
                    source_name: str | None = None, vat_rate: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название сметы")
    lines = parsed.get("lines") or []
    if not lines:
        raise ValueError("В файле не найдено ни одной позиции сметы")
    rate = _dec(vat_rate) if vat_rate not in (None, "") else Decimal("22")
    if rate is None or not rate.is_finite() or not Decimal("0") <= rate <= Decimal("30"):
        raise ValueError("Ставка НДС должна быть от 0 до 30")
    for line in lines:
        for field, scale in (("qty", 3), ("unit_price", 2), ("total", 2)):
            _validate_storable(_dec(line.get(field)), scale)
    from app.provisional_policy import derive_unit_price
    for line in lines:
        q, price, amount = _dec(line.get("qty")), _dec(line.get("unit_price")), _dec(line.get("total"))
        if price is None and q is not None and q > 0 and amount is not None:
            derive_unit_price(q, amount)
    total = _dec(parsed.get("total"))
    if total is None:
        total = sum((_dec(l.get("total")) or ZERO for l in lines), ZERO)
    _validate_storable(total, 2)
    est = Estimate(project_id=project_id, contract_id=contract_id, name=name[:500], source_name=(source_name or "")[:500] or None,
                   total=total, vat_rate=rate)
    db.add(est)
    db.flush()
    for i, l in enumerate(lines, start=1):
        db.add(EstimateLine(
            project_id=project_id, estimate_id=est.id, sort_order=int(l.get("sort_order") or i),
            section=(l.get("section") or None), position=(l.get("position") or None), code=(l.get("code") or None),
            name=(l.get("name") or "").strip() or "—", unit=(l.get("unit") or None),
            qty=_dec(l.get("qty")), unit_price=_dec(l.get("unit_price")), total=_dec(l.get("total")), note=(l.get("note") or None),
        ))
    db.flush()
    return _estimate_dict(est, len(lines))



def list_estimates(db: Session, project_id: int) -> list[dict]:
    rows = db.execute(select(Estimate).where(Estimate.project_id == project_id).order_by(Estimate.id.desc())).scalars().all()
    counts = dict(db.execute(
        select(EstimateLine.estimate_id, func.count()).where(EstimateLine.project_id == project_id).group_by(EstimateLine.estimate_id)
    ).all())
    return [_estimate_dict(e, int(counts.get(e.id, 0))) for e in rows]


def _get(db: Session, project_id: int, estimate_id: int) -> Estimate:
    e = db.get(Estimate, estimate_id)
    if e is None or e.project_id != project_id:
        raise LookupError("Смета не найдена")
    return e


def _fact_by_line(db: Session, project_id: int, estimate_id: int, date_from: date | None = None, date_to: date | None = None) -> dict[int, Decimal]:
    stmt = (
        select(WorkLogEntry.estimate_line_id, func.coalesce(func.sum(WorkLogEntry.qty), 0))
        .join(EstimateLine, EstimateLine.id == WorkLogEntry.estimate_line_id)
        .where(WorkLogEntry.project_id == project_id, EstimateLine.estimate_id == estimate_id)
        .group_by(WorkLogEntry.estimate_line_id)
    )
    if date_from:
        stmt = stmt.where(WorkLogEntry.work_date >= date_from)
    if date_to:
        stmt = stmt.where(WorkLogEntry.work_date <= date_to)
    return {lid: Decimal(str(q)) for lid, q in db.execute(stmt).all()}


def _billed_by_line(db: Session, project_id: int, estimate_id: int, exclude_act: int | None = None,
                    before: date | None = None) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    stmt = select(KsAct).where(KsAct.project_id == project_id, KsAct.estimate_id == estimate_id)
    for act in db.execute(stmt).scalars().all():
        if exclude_act is not None and act.id == exclude_act:
            continue
        if before is not None and act.period_to >= before:
            continue
        for l in json.loads(act.lines_json):
            out[l["line_id"]] = out.get(l["line_id"], ZERO) + Decimal(l["qty"])
    return out


def get_estimate(db: Session, project_id: int, estimate_id: int) -> dict:
    e = _get(db, project_id, estimate_id)
    lines = db.execute(
        select(EstimateLine).where(EstimateLine.estimate_id == e.id).order_by(EstimateLine.sort_order, EstimateLine.id)
    ).scalars().all()
    fact = _fact_by_line(db, project_id, e.id)
    billed = _billed_by_line(db, project_id, e.id)
    out_lines = []
    done_amount = ZERO
    for l in lines:
        f = fact.get(l.id, ZERO)
        price = _unit_price(l)
        pct = None
        if l.qty and l.qty > 0:
            pct = float((f / l.qty * 100).quantize(Decimal("0.1")))
        if price is not None:
            done_amount += f * price
        out_lines.append({
            "id": l.id, "sort_order": l.sort_order, "section": l.section, "position": l.position, "code": l.code,
            "name": l.name, "unit": l.unit, "qty": _s(l.qty, THREE), "unit_price": _s(l.unit_price), "total": _s(l.total),
            "fact_qty": _s(f, THREE), "billed_qty": _s(billed.get(l.id, ZERO), THREE), "done_percent": pct,
        })
    d = _estimate_dict(e, len(lines))
    d["lines"] = out_lines
    d["done_amount"] = _s(_money(done_amount))
    return d


def delete_estimate(db: Session, project_id: int, estimate_id: int) -> None:
    e = _get(db, project_id, estimate_id)
    db.delete(e)
    db.flush()


def update_line(db: Session, project_id: int, line_id: int, data: dict) -> dict:
    l = db.get(EstimateLine, line_id)
    if l is None or l.project_id != project_id:
        raise LookupError("Позиция сметы не найдена")
    changes = {}
    for field in ("name", "unit", "code", "position", "section", "note"):
        if field in data:
            value = (str(data[field]).strip() if data[field] is not None else "")
            if field == "name" and not value:
                raise ValueError("Название позиции не может быть пустым")
            changes[field] = value or None
    for field in ("qty", "unit_price", "total"):
        if field in data:
            value = _dec(data[field])
            if data[field] not in (None, "") and value is None:
                raise ValueError("Число указано неверно")
            _validate_storable(value, 3 if field == "qty" else 2)
            if value is not None and value < 0:
                raise ValueError("Значение не может быть отрицательным")
            changes[field] = value
    q = changes.get("qty", l.qty)
    price = changes.get("unit_price", l.unit_price)
    amount = changes.get("total", l.total)
    if price is None and q is not None and q > 0 and amount is not None:
        from app.provisional_policy import derive_unit_price
        derive_unit_price(q, amount)
    for field, value in changes.items():
        setattr(l, field, value)
    db.flush()
    return {"id": l.id}



def _unit_price(l: EstimateLine) -> Decimal | None:
    if l.unit_price is not None:
        return l.unit_price
    if l.total is not None and l.qty:
        from app.provisional_policy import derive_unit_price
        return derive_unit_price(l.qty, l.total)
    return None


# ----------------------------------------------------------------------------- КС


def _act_dict(a: KsAct) -> dict:
    lines = json.loads(a.lines_json)
    return {
        "id": a.id, "estimate_id": a.estimate_id, "number": a.number,
        "period_from": a.period_from.isoformat(), "period_to": a.period_to.isoformat(),
        "act_date": a.act_date.isoformat() if a.act_date else None, "status": a.status,
        "total_ex_vat": _s(a.total_ex_vat), "lines_count": len(lines),
        "decision_revision": a.decision_revision,
        "customer_status": a.customer_status, "customer_comment": a.customer_comment,
        "customer_decided_at": a.customer_decided_at.isoformat() if a.customer_decided_at else None,
    }


def list_acts(db: Session, project_id: int, estimate_id: int) -> list[dict]:
    _get(db, project_id, estimate_id)
    rows = db.execute(select(KsAct).where(KsAct.project_id == project_id, KsAct.estimate_id == estimate_id)
                      .order_by(KsAct.id)).scalars().all()
    return [_act_dict(a) for a in rows]


def _entry_reservations(db: Session, project_id: int) -> dict[int, Decimal]:
    """Сколько qty каждой записи журнала уже зарезервировано актами проекта (по всем сметам)."""
    out: dict[int, Decimal] = {}
    acts = db.execute(select(KsAct).where(KsAct.project_id == project_id)).scalars().all()
    for act in acts:
        try:
            lines = json.loads(act.lines_json)
        except (ValueError, TypeError):
            continue
        for l in lines:
            allocs = l.get("entry_allocations")
            if not allocs:
                continue
            for a in allocs:
                try:
                    eid = int(a["entry_id"])
                    q = Decimal(str(a["qty"]))
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    continue
                out[eid] = out.get(eid, ZERO) + q
    return out


def _legacy_lines_in_period(db: Session, project_id: int, estimate_id: int, period_from: date, period_to: date) -> set[int]:
    """Строки legacy-актов (без entry_allocations), чей период пересекается с запрошенным."""
    out: set[int] = set()
    acts = db.execute(select(KsAct).where(KsAct.project_id == project_id, KsAct.estimate_id == estimate_id)).scalars().all()
    for act in acts:
        if act.period_to < period_from or act.period_from > period_to:
            continue
        try:
            lines = json.loads(act.lines_json)
        except (ValueError, TypeError):
            continue
        for l in lines:
            if l.get("entry_allocations"):
                continue
            try:
                out.add(int(l["line_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _preview(db: Session, project_id: int, estimate_id: int, period_from: date, period_to: date) -> dict:
    if period_to < period_from:
        raise ValueError("Конец периода раньше начала")
    e = _get(db, project_id, estimate_id)
    lines = db.execute(select(EstimateLine).where(EstimateLine.estimate_id == e.id)
                       .order_by(EstimateLine.sort_order, EstimateLine.id)).scalars().all()
    line_by_id = {l.id: l for l in lines}
    billed = _billed_by_line(db, project_id, e.id)
    reserved = _entry_reservations(db, project_id)
    legacy_lines = _legacy_lines_in_period(db, project_id, e.id, period_from, period_to)
    entries = db.execute(
        select(WorkLogEntry).where(
            WorkLogEntry.project_id == project_id,
            WorkLogEntry.estimate_line_id.in_(list(line_by_id.keys())) if line_by_id else False,
            WorkLogEntry.work_date >= period_from,
            WorkLogEntry.work_date <= period_to,
        ).order_by(WorkLogEntry.work_date, WorkLogEntry.id)
    ).scalars().all()
    warnings: list[str] = []
    # Свободный остаток по каждой записи: qty минус уже зарезервированное актами.
    free: dict[int, Decimal] = {}
    for en in entries:
        q = en.qty if en.qty is not None else ZERO
        if q <= 0:
            continue
        left = q - reserved.get(en.id, ZERO)
        if left > 0:
            free[en.id] = left
    # Остаток сметного объёма по каждой строке (existing billed cap).
    remaining: dict[int, Decimal | None] = {}
    for l in lines:
        if l.qty is None:
            remaining[l.id] = None
        else:
            remaining[l.id] = max(ZERO, l.qty - billed.get(l.id, ZERO))
    # Deterministic allocation по work_date/id, ограниченный cap.
    alloc_by_line: dict[int, list[dict]] = {}
    qty_by_line: dict[int, Decimal] = {}
    for en in entries:
        if en.id not in free:
            continue
        lid = en.estimate_line_id
        if lid not in line_by_id:
            continue
        if lid in legacy_lines:
            continue
        cap = remaining.get(lid)
        if cap is not None and cap <= 0:
            continue
        take = free[en.id]
        if cap is not None and take > cap:
            take = cap
        if take <= 0:
            continue
        alloc_by_line.setdefault(lid, []).append({"entry_id": en.id, "qty": f"{take.quantize(THREE):f}"})
        qty_by_line[lid] = qty_by_line.get(lid, ZERO) + take
        if cap is not None:
            remaining[lid] = cap - take
    # Предупреждение о превышении остатка сметного объёма: сумма положительного свободного
    # факта по строке больше оставшегося сметного объёма (включая cap == 0 при наличии факта).
    # Сравниваем только свободный (не зарезервированный) факт, чтобы не давать ложных срабатываний.
    free_by_line: dict[int, Decimal] = {}
    for en in entries:
        if en.id not in free:
            continue
        lid = en.estimate_line_id
        if lid not in line_by_id or lid in legacy_lines:
            continue
        free_by_line[lid] = free_by_line.get(lid, ZERO) + free[en.id]
    for lid, free_qty in free_by_line.items():
        l = line_by_id.get(lid)
        if l is None or l.qty is None:
            continue
        left = max(ZERO, l.qty - billed.get(lid, ZERO))
        if free_qty > left:
            warnings.append(f"Позиция {l.position or l.id} «{l.name[:60]}»: свободный факт превышает остаток сметного объёма "
                            f"({_s(free_qty, THREE)} > {_s(left, THREE)}), в акт включён остаток сметного объёма.")
    for lid in legacy_lines:
        l = line_by_id.get(lid)
        if l is None:
            continue
        warnings.append(f"Позиция {l.position or l.id} «{l.name[:60]}»: есть legacy-акт за пересекающийся период без "
                        f"распределения по записям журнала — источник факта не определён, строка исключена из сверки.")
    out: list[dict] = []
    for l in lines:
        qty = qty_by_line.get(l.id, ZERO)
        if qty <= 0:
            continue
        price = _unit_price(l)
        if price is None:
            warnings.append(f"Позиция {l.position or l.id} «{l.name[:60]}»: в смете нет цены, в акт не включена.")
            continue
        already = billed.get(l.id, ZERO)
        out.append({
            "line_id": l.id, "position": l.position or "", "code": l.code or "", "section": l.section or "", "name": l.name,
            "unit": l.unit or "", "qty": f"{qty.quantize(THREE):f}", "unit_price": f"{price:f}",
            "amount": f"{_money(qty * price):f}", "estimate_qty": _s(l.qty, THREE), "prev_qty": f"{already.quantize(THREE):f}",
            "entry_allocations": alloc_by_line.get(l.id, []),
        })
    total = _money(sum((Decimal(l["amount"]) for l in out), ZERO))
    return {"lines": out, "total_ex_vat": f"{total:f}", "warnings": warnings}



def preview_act(db: Session, project_id: int, estimate_id: int, period_from: date, period_to: date) -> dict:
    """Что войдёт в КС-2 за период: небиллированный остаток каждой записи журнала, не больше остатка сметы."""
    return _preview(db, project_id, estimate_id, period_from, period_to)



def create_act(db: Session, project_id: int, estimate_id: int, period_from: date, period_to: date,
               number: str | None = None, act_date: date | None = None) -> dict:
    # Сериализация конкурирующих созданий по проекту: блокировка строки проекта до commit вызывающего.
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    prev = _preview(db, project_id, estimate_id, period_from, period_to)
    if not prev["lines"]:
        legacy_warnings = [w for w in prev["warnings"] if "legacy-акт" in w]
        if legacy_warnings:
            raise ValueError(
                "За период нет новых выполненных работ по позициям сметы: строки исключены из сверки из-за legacy-акта "
                "за пересекающийся период без распределения по записям журнала — источник факта не определён. "
                + " ".join(legacy_warnings)
            )
        raise ValueError("За период нет новых выполненных работ по позициям сметы: внесите факт в журнале работ и выберите позицию сметы.")
    existing = db.scalar(select(func.count()).select_from(KsAct).where(KsAct.project_id == project_id, KsAct.estimate_id == estimate_id)) or 0
    act = KsAct(
        project_id=project_id, estimate_id=estimate_id, number=(number or str(existing + 1)).strip()[:40],
        period_from=period_from, period_to=period_to, act_date=act_date or period_to, status="draft",
        lines_json=json.dumps(prev["lines"], ensure_ascii=False), total_ex_vat=Decimal(prev["total_ex_vat"]),
    )
    db.add(act)
    db.flush()
    d = _act_dict(act)
    d["warnings"] = prev["warnings"]
    return d



def _get_act(db: Session, project_id: int, act_id: int) -> KsAct:
    a = db.get(KsAct, act_id)
    if a is None or a.project_id != project_id:
        raise LookupError("Акт не найден")
    return a


def set_act_status(db: Session, project_id: int, act_id: int, status: str, header: dict | None = None) -> dict:
    a = _get_act(db, project_id, act_id)
    if status not in {"draft", "sent", "signed"}:
        raise ValueError("Статус акта: draft, sent или signed")
    if a.status == "signed" and status != "signed":
        raise ValueError("Подписанный акт изменить нельзя")
    values = {"status": status, "decision_revision": KsAct.decision_revision + 1}
    if header is not None:
        values["header_json"] = json.dumps({k: v for k, v in header.items() if v}, ensure_ascii=False)
    if status == "draft":
        values.update(customer_status="pending", customer_comment=None)
    stmt = update(KsAct).where(KsAct.id == act_id, KsAct.project_id == project_id)
    if status != "signed":
        stmt = stmt.where(KsAct.status != "signed")
    result = db.execute(stmt.values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        exists = db.scalar(select(KsAct.id).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
        ))
        if exists is None:
            raise LookupError("Акт не найден")
        raise ValueError("Подписанный акт изменить нельзя")
    db.refresh(a)
    db.flush()
    return _act_dict(a)


def delete_act(db: Session, project_id: int, act_id: int) -> None:
    a = _get_act(db, project_id, act_id)
    if a.status == "signed":
        raise ValueError("Подписанный акт удалить нельзя")
    # A cached draft may have been signed by another transaction.
    # Check the invariant atomically in the DELETE predicate.
    result = db.execute(
        delete(KsAct).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
            KsAct.status != "signed",
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        status = db.scalar(select(KsAct.status).where(
            KsAct.id == act_id, KsAct.project_id == project_id,
        ))
        if status is None:
            raise LookupError("Акт не найден")
        raise ValueError("Подписанный акт удалить нельзя")
    db.expunge(a)
    db.flush()


def act_with_totals(db: Session, project_id: int, act_id: int) -> dict:
    """Акт со строками и данными для КС-3: с начала работ, с начала года, за период."""
    a = _get_act(db, project_id, act_id)
    e = _get(db, project_id, a.estimate_id)
    lines = json.loads(a.lines_json)
    others = db.execute(select(KsAct).where(KsAct.project_id == project_id, KsAct.estimate_id == a.estimate_id, KsAct.id != a.id,
                                            KsAct.period_to < a.period_from)).scalars().all()
    prev_total = ZERO
    prev_year = ZERO
    for o in others:
        amount = sum((Decimal(l["amount"]) for l in json.loads(o.lines_json)), ZERO)
        prev_total += amount
        if o.period_to.year == a.period_to.year:
            prev_year += amount
    period_total = Decimal(str(a.total_ex_vat))
    rate = Decimal(str(e.vat_rate))
    by_section: dict[str, Decimal] = {}
    for l in lines:
        by_section[l["section"] or "Работы по смете"] = by_section.get(l["section"] or "Работы по смете", ZERO) + Decimal(l["amount"])
    return {
        "act": _act_dict(a), "estimate": _estimate_dict(e), "lines": lines, "sections": {k: _s(v) for k, v in by_section.items()},
        "period_total": _s(period_total), "from_start": _s(_money(prev_total + period_total)),
        "from_year_start": _s(_money(prev_year + period_total)), "vat_rate": _s(rate),
        "vat": _s(_money(period_total * rate / Decimal("100"))),
        "total_with_vat": _s(_money(period_total * (Decimal("100") + rate) / Decimal("100"))),
    }
