from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.acceptance_act_document import (
    _CONTENT_TYPES,
    _DOC_RELS,
    _RELS,
    _W_NS,
    _MONTHS_GEN,
    _para,
    _table,
)

PLACEHOLDER = "________"


@dataclass
class OrgRequisites:
    name: str
    ogrn: Optional[str] = None
    inn: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    sro_name: Optional[str] = None
    sro_ogrn: Optional[str] = None
    sro_inn: Optional[str] = None
    is_individual_entrepreneur: bool = False


@dataclass
class Representative:
    role: str
    position: Optional[str]
    full_name: str
    order_ref: Optional[str]
    nrs_id: Optional[str] = None
    organization: Optional[OrgRequisites] = None


@dataclass
class Material:
    name: str
    quality_doc: Optional[str]


@dataclass
class AosrData:
    object_name: str
    object_address: Optional[str]
    number: str
    act_date: Optional[date]
    developer: OrgRequisites
    builder: OrgRequisites
    designer: Optional[OrgRequisites]
    representatives: list[Representative]
    works: str
    design_docs: Optional[str]
    materials: list[Material]
    supporting_docs: list[str]
    work_start: Optional[date]
    work_finish: Optional[date]
    norms: list[str]
    next_works: Optional[str]
    additional: Optional[str] = None
    copies: Optional[int] = None
    attachments: list[str] = field(default_factory=list)
    nrs_required: bool = True


ROLE_TITLES: dict[str, str] = {
    "customer_control": (
        "Представитель застройщика, технического заказчика, лица, ответственного "
        "за эксплуатацию здания, сооружения, или регионального оператора по вопросам "
        "строительного контроля"
    ),
    "builder": "Представитель лица, осуществляющего строительство",
    "builder_control": (
        "Представитель лица, осуществляющего строительство, по вопросам строительного контроля"
    ),
    "designer": (
        "Представитель лица, осуществляющего подготовку проектной документации"
    ),
    "performer": "Представитель лица, выполнившего работы",
}

_REQUIRED_ROLES = ("customer_control", "builder", "builder_control")

_NRS_REQUIRED_ROLES = ("customer_control", "builder_control")


def _missing_or_placeholder(value: Optional[str]) -> bool:
    return value is None or value.strip() == ""


def _org_missing(prefix: str, org: OrgRequisites) -> list[str]:
    out: list[str] = []
    ogrn_label = "ОГРНИП" if org.is_individual_entrepreneur else "ОГРН"
    if _missing_or_placeholder(org.name):
        out.append(f"{prefix}: нет наименования")
    if _missing_or_placeholder(org.ogrn):
        out.append(f"{prefix}: нет {ogrn_label}")
    if _missing_or_placeholder(org.inn):
        out.append(f"{prefix}: нет ИНН")
    return out


def aosr_missing(data: AosrData) -> list[str]:
    missing: list[str] = []

    if _missing_or_placeholder(data.object_name):
        missing.append("Не указано наименование объекта")
    if _missing_or_placeholder(data.object_address):
        missing.append("Не указан адрес объекта")

    missing.extend(_org_missing("Застройщик", data.developer))
    missing.extend(_org_missing("Лицо, осуществляющее строительство", data.builder))

    roles_present = {r.role for r in data.representatives}
    for role in _REQUIRED_ROLES:
        if role not in roles_present:
            missing.append(f"Нет представителя: {ROLE_TITLES[role]}")

    for rep in data.representatives:
        title = ROLE_TITLES.get(rep.role, rep.role)
        if _missing_or_placeholder(rep.order_ref):
            missing.append(
                f"{title}: не указан распорядительный документ (приказ) о полномочиях"
            )
        if data.nrs_required and rep.role in _NRS_REQUIRED_ROLES:
            if _missing_or_placeholder(rep.nrs_id):
                missing.append(
                    f"{title}: нет ИНРС (идентификационного номера в национальном "
                    f"реестре специалистов)"
                )

    if _missing_or_placeholder(data.design_docs):
        missing.append("Не указана проектная/рабочая документация (п. 2)")

    for m in data.materials:
        if _missing_or_placeholder(m.quality_doc):
            missing.append(f"Материал «{m.name}»: нет документа о качестве")

    if data.work_start is None or data.work_finish is None:
        missing.append("Нет дат начала/окончания работ (п. 5)")

    if not data.norms:
        missing.append("Не указаны нормативные документы (п. 6)")

    if _missing_or_placeholder(data.next_works):
        missing.append("Не указаны последующие работы (п. 7)")

    if data.act_date is None:
        missing.append("Не указана дата акта")

    return missing


def _val(value: Optional[str]) -> str:
    if value is None:
        return PLACEHOLDER
    s = str(value)
    return s if s.strip() else PLACEHOLDER


def _date_words(d: Optional[date]) -> str:
    if d is None:
        return PLACEHOLDER
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year} года"


def _date_short(d: Optional[date]) -> str:
    if d is None:
        return PLACEHOLDER
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year}"


def _org_block(org: Optional[OrgRequisites], heading: str) -> list[str]:
    lines: list[str] = [heading]
    if org is None:
        lines.append(_val(None))
        return lines
    lines.append(f"Наименование: {_val(org.name)}")
    ogrn_label = "ОГРНИП" if org.is_individual_entrepreneur else "ОГРН"
    lines.append(f"{ogrn_label}: {_val(org.ogrn)}")
    lines.append(f"ИНН: {_val(org.inn)}")
    lines.append(f"Адрес: {_val(org.address)}")
    lines.append(f"Телефон: {_val(org.phone)}")
    if org.sro_name is not None or org.sro_ogrn is not None or org.sro_inn is not None:
        lines.append("СРО:")
        lines.append(f"  Наименование: {_val(org.sro_name)}")
        lines.append(f"  ОГРН: {_val(org.sro_ogrn)}")
        lines.append(f"  ИНН: {_val(org.sro_inn)}")
    return lines


def _rep_line(rep: Representative) -> str:
    title = ROLE_TITLES.get(rep.role, rep.role)
    parts = [f"должность: {_val(rep.position)}", f"ФИО: {_val(rep.full_name)}"]
    parts.append(f"полномочия: {_val(rep.order_ref)}")
    if rep.role in _NRS_REQUIRED_ROLES:
        parts.append(f"ИНРС: {_val(rep.nrs_id)}")
    if rep.organization is not None:
        org_bits = [f"организация: {_val(rep.organization.name)}"]
        if rep.organization.inn:
            org_bits.append(f"ИНН: {rep.organization.inn}")
        parts.append(", ".join(org_bits))
    return f"{title}: {', '.join(parts)}"


def aosr_lines(data: AosrData) -> dict:
    title = f"АКТ освидетельствования скрытых работ № {data.number}"

    object_line = f"Объект капитального строительства: {_val(data.object_name)}"
    object_address_line = f"Адрес объекта: {_val(data.object_address)}"

    developer_block = _org_block(data.developer, "Застройщик (технический заказчик):")
    builder_block = _org_block(
        data.builder, "Лицо, осуществляющее строительство:"
    )
    designer_block: list[str] = []
    if data.designer is not None:
        designer_block = _org_block(
            data.designer, "Лицо, осуществляющее подготовку проектной документации:"
        )

    number_date_line = (
        f"Номер акта: {_val(data.number)}. "
        f"Дата составления: {_date_short(data.act_date)}"
    )

    representatives_lines = [_rep_line(r) for r in data.representatives]

    performer_org = None
    for r in data.representatives:
        if r.role == "performer" and r.organization is not None:
            performer_org = r.organization
            break
    performer_name = performer_org.name if performer_org else PLACEHOLDER
    intro_phrase = (
        "произвели осмотр работ, выполненных "
        f"{_val(performer_name)}, и составили настоящий акт о нижеследующем:"
    )

    p1 = f"1. Работы: {_val(data.works)}"
    p2 = f"2. Проектная/рабочая документация: {_val(data.design_docs)}"

    materials_registry: list[list[str]] = []
    if len(data.materials) > 5:
        p3 = (
            "3. Материалы и изделия: согласно реестру материалов и документов "
            "о качестве (приложение к акту)."
        )
        materials_registry.append(
            ["№", "Наименование материала", "Документ о качестве"]
        )
        for idx, m in enumerate(data.materials, start=1):
            materials_registry.append(
                [str(idx), _val(m.name), _val(m.quality_doc)]
            )
    else:
        material_parts: list[str] = []
        for m in data.materials:
            material_parts.append(
                f"{_val(m.name)} — {_val(m.quality_doc)}"
            )
        materials_text = "; ".join(material_parts) if material_parts else PLACEHOLDER
        p3 = f"3. Материалы и изделия: {materials_text}"

    if data.supporting_docs:
        p4 = "4. Документы, подтверждающие соответствие работ: " + "; ".join(
            data.supporting_docs
        )
    else:
        p4 = "4. Документы, подтверждающие соответствие работ: " + PLACEHOLDER

    p5 = (
        "5. Сроки выполнения работ: "
        f"начало — {_date_short(data.work_start)}; "
        f"окончание — {_date_short(data.work_finish)}"
    )

    if data.norms:
        p6 = "6. Нормативные документы: " + "; ".join(data.norms)
    else:
        p6 = "6. Нормативные документы: " + PLACEHOLDER

    p7 = f"7. Последующие работы: {_val(data.next_works)}"

    additional_line = f"Дополнительные сведения: {_val(data.additional)}"

    copies = data.copies if data.copies is not None else len(data.representatives)
    copies_line = f"Акт составлен в {copies} экземплярах"

    attachments_combined = list(data.attachments)
    if materials_registry:
        attachments_combined.append(
            "Реестр материалов и документов о качестве"
        )
    if attachments_combined:
        attachments_line = "Приложения: " + "; ".join(attachments_combined)
    else:
        attachments_line = "Приложения: " + PLACEHOLDER

    signatures = [
        {
            "role": ROLE_TITLES.get(r.role, r.role),
            "full_name": _val(r.full_name),
            "signature": "__________ (подпись)",
        }
        for r in data.representatives
    ]

    return {
        "title": title,
        "object": object_line,
        "object_address": object_address_line,
        "developer": developer_block,
        "builder": builder_block,
        "designer": designer_block,
        "number_date": number_date_line,
        "representatives": representatives_lines,
        "intro_phrase": intro_phrase,
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,
        "p5": p5,
        "p6": p6,
        "p7": p7,
        "additional": additional_line,
        "copies": copies_line,
        "attachments": attachments_line,
        "signatures": signatures,
        "materials_registry": materials_registry,
    }


def _page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def render_aosr_docx(data: AosrData) -> bytes:
    lines = aosr_lines(data)

    paragraphs: list[str] = []
    paragraphs.append(_para(lines["title"], bold=True, align="center"))
    paragraphs.append(_para(""))
    paragraphs.append(_para(lines["object"]))
    paragraphs.append(_para(lines["object_address"]))
    paragraphs.append(_para(""))

    for block_key in ("developer", "builder", "designer"):
        block = lines[block_key]
        if not block:
            continue
        for i, ln in enumerate(block):
            paragraphs.append(_para(ln, bold=(i == 0)))
        paragraphs.append(_para(""))

    paragraphs.append(_para(lines["number_date"]))
    paragraphs.append(_para(""))
    for ln in lines["representatives"]:
        paragraphs.append(_para(ln))
    paragraphs.append(_para(""))
    paragraphs.append(_para(lines["intro_phrase"]))
    paragraphs.append(_para(""))
    for key in ("p1", "p2", "p3", "p4", "p5", "p6", "p7"):
        paragraphs.append(_para(lines[key]))
    paragraphs.append(_para(""))
    paragraphs.append(_para(lines["additional"]))
    paragraphs.append(_para(lines["copies"]))
    paragraphs.append(_para(lines["attachments"]))
    paragraphs.append(_para(""))

    for sig in lines["signatures"]:
        paragraphs.append(_para(sig["role"]))
        paragraphs.append(_para(f"{sig['full_name']}  {sig['signature']}"))
        paragraphs.append(_para(""))

    if lines["materials_registry"]:
        paragraphs.append(_page_break())
        paragraphs.append(
            _para(
                "Реестр материалов и документов о качестве",
                bold=True,
                align="center",
            )
        )
        paragraphs.append(_para(""))
        paragraphs.append(_table(lines["materials_registry"]))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
    return buf.getvalue()
