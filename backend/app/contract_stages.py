"""Extraction of contract stages (этапы) from contract text and its attachments."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.contract_document_link import ContractDocumentLink
from app.models.contract_stage import ContractStage
from app.models.document import Document
from app.models.document_version import DocumentVersion


_MAX_TEXT_CHARS = 200_000


def _document_text(db: Session, document_id: int) -> str | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == doc.id,
        DocumentVersion.version_number == doc.current_version,
    ).first()
    if version is None:
        return None
    return (version.content or "") or None


def contract_text(db: Session, project_id: int, contract) -> list[tuple[int, str, str]]:
    """Return [(document_id, name, text)] for the main contract doc and its linked docs."""
    result: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    doc_ids: list[int] = []
    if getattr(contract, "source_document_id", None):
        doc_ids.append(contract.source_document_id)
    links = db.query(ContractDocumentLink).filter(
        ContractDocumentLink.contract_id == contract.id,
    ).all()
    for link in links:
        doc_ids.append(link.document_id)

    total_chars = 0
    for doc_id in doc_ids:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        text = _document_text(db, doc_id)
        if not text:
            continue
        doc = db.get(Document, doc_id)
        name = getattr(doc, "name", None) or getattr(doc, "title", None) or ""
        if total_chars + len(text) > _MAX_TEXT_CHARS:
            remaining = _MAX_TEXT_CHARS - total_chars
            if remaining <= 0:
                break
            text = text[:remaining]
        total_chars += len(text)
        result.append((doc_id, str(name), text))
        if total_chars >= _MAX_TEXT_CHARS:
            break
    return result


_DATE_RE = r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})"
_AMOUNT_RE = r"(\d[\d ]*(?:[.,]\d{1,2})?)"
_STAGE_START_RE = re.compile(r"^\s*(\d+)\s*(?:-?\s*(?:й|ой|ий))?\s+этап\b", re.IGNORECASE)


def _parse_amount(raw: str) -> Decimal | None:
    cleaned = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw: str) -> date | None:
    parts = re.split(r"[./]", raw)
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2])
        if year < 100:
            year += 2000
        return date(year, month, day)
    except ValueError:
        return None


_STAGE_RE = re.compile(
    r"(?<![\d.,])(\d{1,2})\s*-?\s*(?:й|ый|ой)?\s+этап\b"
    r"((?:(?!\d{1,2}\s*-?\s*(?:й|ый|ой)?\s+этап\b).){0,3000}?)"
    r"(\d{2}\.\d{2}\.\d{4})\s*(?:г\.?)?\s*[-–—]?\s*(\d{2}\.\d{2}\.\d{4})\s*(?:г\.?)?\s+"
    r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:,\d{2})?)(?![\d])",
    re.IGNORECASE | re.DOTALL,
)
_OBJECT_RE = re.compile(
    r"([А-ЯЁA-Z][А-ЯЁA-Z0-9\-\. ]{2,80}?)\s+Начало\s+Окончание\s+Стоимость",
)


def parse_stages_regex(text: str) -> list[dict]:
    """Stages «N этап … дд.мм.гггг дд.мм.гггг 1 234,56» from flat or multi-line text.

    Document texts are stored whitespace-collapsed, so this scans the whole
    text instead of lines.  An UPPER-CASE heading right before «Начало
    Окончание Стоимость» starts a new object (site) whose stages renumber.
    """
    flat = re.sub(r"\s+", " ", text or "")
    headers = [(m.start(), m.group(1).strip()) for m in _OBJECT_RE.finditer(flat)]
    stages: list[dict] = []
    for match in _STAGE_RE.finditer(flat):
        amount = _parse_amount(match.group(5))
        if amount is None or amount <= 0:
            continue
        object_name = None
        for position, name in headers:
            if position < match.start():
                object_name = name
        stages.append({
            "object_name": object_name,
            "stage_number": int(match.group(1)),
            "title": match.group(2).strip(" ;,.-"),
            "planned_start": _parse_date(match.group(3)),
            "planned_finish": _parse_date(match.group(4)),
            "amount": amount,
            "evidence_quote": match.group(0).strip(),
        })
    return stages


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _digits_contain(quote: str, amount: Decimal) -> bool:
    """The quote must carry the exact amount: rubles and kopecks (1 234,50 / 1234.50 / 1 234)."""
    digits_quote = re.sub(r"[^\d]", "", quote)
    rubles, kopecks = f"{amount:.2f}".split(".")
    candidates = {rubles + kopecks}
    if kopecks == "00":
        candidates.add(rubles)
    return any(candidate in digits_quote for candidate in candidates)


_SCHEMA = {
    "stages": [
        {
            "object_name": "str|null",
            "stage_number": "int",
            "title": "str",
            "planned_start": "YYYY-MM-DD|null",
            "planned_finish": "YYYY-MM-DD|null",
            "amount": "number",
            "evidence_quote": "str",
        }
    ]
}

_SYSTEM_PROMPT = (
    "Найди в договоре и приложениях этапы работ со сроками и стоимостью. "
    "Только то, что есть в тексте дословно; evidence_quote — дословный фрагмент строки этапа."
)


def _coerce_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def extract_stages_ai(texts: list[tuple[int, str, str]]) -> list[dict] | None:
    try:
        from app import gemini_analysis
    except Exception:
        return None
    try:
        if not gemini_analysis.deepseek_configured():
            return None
    except Exception:
        return None
    combined = "\n\n".join(t for _, _, t in texts)
    if not combined.strip():
        return None
    try:
        response = gemini_analysis._try_deepseek(_SYSTEM_PROMPT, combined, _SCHEMA)
    except Exception:
        return None
    if not_response_ok(response):
        return None
    raw_stages = response.get("stages") if isinstance(response, dict) else None
    if not isinstance(raw_stages, list):
        return None
    normalized_combined = _normalize(combined)
    accepted: list[dict] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("stage_number"))
        except (TypeError, ValueError):
            continue
        title = (item.get("title") or "").strip()
        quote = (item.get("evidence_quote") or "").strip()
        if not title or not quote:
            continue
        try:
            amount = Decimal(str(item.get("amount"))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            continue
        if not _digits_contain(quote, amount):
            continue
        if _normalize(quote) not in normalized_combined:
            continue
        accepted.append({
            "object_name": (item.get("object_name") or None),
            "stage_number": number,
            "title": title,
            "planned_start": _coerce_date(item.get("planned_start")),
            "planned_finish": _coerce_date(item.get("planned_finish")),
            "amount": amount,
            "evidence_quote": quote,
        })
    return accepted or None


def not_response_ok(response) -> bool:
    try:
        return isinstance(response, dict)
    except Exception:
        return False


def _sum_amounts(stages: list[dict]) -> Decimal:
    total = Decimal("0.00")
    for st in stages:
        amount = st.get("amount")
        if isinstance(amount, Decimal):
            total += amount
    return total


def renumber_in_order(stages: list[dict]) -> tuple[list[dict], list[str]]:
    """Stages go in document order; a typo in the printed numbers (3 → 5) must not
    leave a hole. Each object is renumbered 1..N; every gap or repeat becomes a warning."""
    warnings: list[str] = []
    counters: dict[str | None, int] = {}
    result: list[dict] = []
    for stage in stages:
        obj = stage.get("object_name")
        counters[obj] = counters.get(obj, 0) + 1
        ordinal = counters[obj]
        printed = stage.get("stage_number")
        if printed != ordinal:
            where = f" ({obj})" if obj else ""
            warnings.append(
                f"В документе{where} этап напечатан как «{printed}-й», по порядку это {ordinal}-й — принят номер {ordinal}."
            )
        result.append({**stage, "stage_number": ordinal})
    return result, warnings


def extract_contract_stages(db: Session, project_id: int, contract) -> dict:
    texts = contract_text(db, project_id, contract)
    method = "none"
    chosen: list[dict] = []
    source_document_id: int | None = None

    ai = extract_stages_ai(texts) if texts else None
    if ai:
        method = "ai"
        chosen = ai
        if texts:
            source_document_id = texts[0][0]
    else:
        best: list[dict] = []
        best_doc_id: int | None = None
        for doc_id, _name, text in texts:
            parsed = parse_stages_regex(text)
            if len(parsed) > len(best):
                best = parsed
                best_doc_id = doc_id
        if best:
            method = "regex"
            chosen = best
            source_document_id = best_doc_id

    chosen, numbering_warnings = renumber_in_order(chosen)

    confirmed = db.query(ContractStage).filter(
        ContractStage.contract_id == contract.id,
        ContractStage.status == "confirmed",
    ).all()
    confirmed_keys = {(s.object_name, s.stage_number) for s in confirmed}

    db.query(ContractStage).filter(
        ContractStage.contract_id == contract.id,
        ContractStage.status == "proposed",
    ).delete()
    db.flush()

    created: list[ContractStage] = []
    for st in chosen:
        key = (st.get("object_name"), st.get("stage_number"))
        if key in confirmed_keys:
            continue
        stage = ContractStage(
            project_id=project_id,
            contract_id=contract.id,
            object_name=st.get("object_name"),
            stage_number=st.get("stage_number"),
            title=st.get("title") or "",
            planned_start=st.get("planned_start"),
            planned_finish=st.get("planned_finish"),
            amount=st.get("amount") or Decimal("0.00"),
            vat_included=True,
            evidence_quote=st.get("evidence_quote"),
            source_document_id=source_document_id,
            status="proposed",
        )
        db.add(stage)
        created.append(stage)
    db.flush()

    all_stages = db.query(ContractStage).filter(ContractStage.contract_id == contract.id).all()
    total = sum((s.amount or Decimal("0.00")) for s in all_stages) if all_stages else Decimal("0.00")
    by_object: dict[str, Decimal] = {}
    for s in all_stages:
        key = s.object_name or ""
        by_object[key] = by_object.get(key, Decimal("0.00")) + (s.amount or Decimal("0.00"))

    db.add(AuditLog(
        action="contract_stages_extracted",
        entity_type="contract",
        entity_id=contract.id,
        details=f"method={method}; count={len(created)}; renumbered={len(numbering_warnings)}",
    ))

    return {
        "method": method,
        "stages": [
            {
                "object_name": s.object_name,
                "stage_number": s.stage_number,
                "title": s.title,
                "planned_start": s.planned_start,
                "planned_finish": s.planned_finish,
                "amount": s.amount,
            }
            for s in created
        ],
        "total": str(total),
        "by_object": {k: str(v) for k, v in by_object.items()},
        "warnings": numbering_warnings,
    }
