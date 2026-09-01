from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Contract
from app.models.user import User


router = APIRouter(tags=["contracts"])


class ContractDiscoveryRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)


_NUMBER_RE = re.compile(
    r"(?:договор|контракт)\s*(?:(?:поставки|подряда|субподряда|оказания\s+услуг)\s*)?"
    r"(?:№|N|номер)?\s*[:№N-]?\s*"
    r"([A-ZА-ЯЁ0-9][A-ZА-ЯЁa-zа-яё0-9./_-]{2,80})",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r"\b((?:ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|ГУП|МУП)\s*[«\"']?[^\n,;]{2,100}?[»\"']?)"
    r"(?=\s*(?:,|именуем|в лице|$))",
    re.IGNORECASE,
)


def _text_for_document(db: Session, document: Document) -> str:
    return db.scalar(select(DocumentVersion.content).where(
        DocumentVersion.document_id == document.id,
    ).order_by(DocumentVersion.version_number.desc()).limit(1)) or ""


def _normalized_reference(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (value or "").casefold())


def _referenced_existing_contract(content: str, contracts: list[Contract], excluded_id: int | None = None) -> Contract | None:
    """Find an existing parent explicitly referenced in the OCR text."""
    body = _normalized_reference(content)
    candidates = []
    for contract in contracts:
        if contract.id == excluded_id:
            continue
        number = _normalized_reference(contract.number)
        if len(number) >= 4 and number in body:
            candidates.append((len(number), contract))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


_PARTY_RE = re.compile(
    r"(?P<org>(?:(?:общество\s+с\s+ограниченной\s+ответственностью)|ООО|АО|ПАО|ЗАО|ФКУ|ФГУП)"
    r"\s+.{1,220}?)\s*,?\s*именуем\w*\s+(?:в\s+дальнейшем\s+)?[«\"']?"
    r"(?P<role>заказчик|подрядчик)[»\"']?",
    re.IGNORECASE,
)


def _organization_key(value: str) -> str:
    quoted = re.findall(r"[«\"]([^»\"]{2,100})[»\"]", value)
    candidate = min(quoted, key=len) if quoted else value
    candidate = re.sub(
        r"\b(?:общество\s+с\s+ограниченной\s+ответственностью|ООО|АО|ПАО|ЗАО|ФКУ|ФГУП)\b",
        " ", candidate, flags=re.IGNORECASE,
    )
    return _normalized_reference(candidate)


def _contract_parties(content: str) -> dict[str, str]:
    parties: dict[str, str] = {}
    compact = " ".join((content or "").split())[:8_000]
    for match in _PARTY_RE.finditer(compact):
        key = _organization_key(match.group("org"))
        if key:
            parties[match.group("role").casefold()] = key
    return parties


def _party_chain_parent(content: str, contract_contents: list[tuple[Contract, str]], excluded_id: int | None = None) -> Contract | None:
    child_customer = _contract_parties(content).get("заказчик")
    if not child_customer:
        return None
    matches = []
    for contract, parent_content in contract_contents:
        if contract.id == excluded_id:
            continue
        parent_contractor = _contract_parties(parent_content).get("подрядчик")
        if parent_contractor and parent_contractor == child_customer:
            matches.append(contract)
    return matches[0] if len(matches) == 1 else None


def discover_contract_fields(name: str, content: str) -> dict:
    """Return a reviewable proposal. It never creates or links records."""
    text = " ".join((content or "").split())
    lowered = f"{name}\n{text[:20_000]}".casefold()
    number_match = _NUMBER_RE.search(text[:12_000]) or _NUMBER_RE.search(name)
    if number_match:
        candidate = number_match.group(1).strip(" .,:;№")
        # OCR frequently turns headings such as "договором" into a bogus number "ом".
        # A usable automatic number must contain a digit; otherwise the filename is safer.
        if not any(character.isdigit() for character in candidate):
            number_match = None
    fallback_number = Path(name).stem.strip()[:255]
    number = (number_match.group(1).strip(" .,:;№") if number_match else fallback_number) or "Без номера"
    companies = []
    for value in _COMPANY_RE.findall(text[:15_000]):
        normalized = " ".join(value.split()).strip(" .,:;")
        if normalized.casefold() not in {item.casefold() for item in companies}:
            companies.append(normalized)

    attachment_name = bool(re.search(
        r"(?:^|\W)(?:приложени|спецификац|график|ведомост|смет|техническ.*задани)",
        Path(name).stem.casefold(),
    ))
    heading = f"{name}\n{text[:1_800]}".casefold()
    if not attachment_name and (("государственн" in heading and "контракт" in heading) or "генподряд" in heading or re.search(r"(?:^|\W)гк[-_№\s]", name.casefold())):
        kind, kind_reason = "prime_reference", "найден государственный/генподрядный контекст"
    elif "поставк" in lowered or "поставщик" in lowered:
        kind, kind_reason = "supply", "найдены признаки договора поставки"
    elif "субсубподряд" in heading or "субподрядчик" in heading or "субподряд" in heading:
        kind, kind_reason = "downstream_subcontract", "найдены признаки субподряда"
    else:
        kind, kind_reason = "customer", "роль сторон требует проверки пользователя"

    legal_markers = sum(marker in lowered for marker in (
        "предмет договора", "права и обязанности", "цена договора", "срок действия",
        "реквизиты сторон", "заказчик", "подрядчик",
    ))
    confidence = min(0.95, 0.35 + (0.25 if number_match else 0) + legal_markers * 0.06)
    is_contract = not attachment_name and (bool(number_match) or legal_markers >= 2)
    return {
        "number": number,
        "title": fallback_number,
        "counterparty": companies[-1] if companies else None,
        "contract_kind": kind,
        "confidence": round(confidence, 2),
        "is_contract": is_contract,
        "evidence": [kind_reason, *( ["номер найден в тексте"] if number_match else ["номер взят из имени файла"]),
                     f"юридических признаков: {legal_markers}",
                     *( ["файл похож на приложение, а не на самостоятельный договор"] if attachment_name else [])],
    }


@router.post("/projects/{project_id}/contracts/discover-bulk")
def discover_contracts_bulk(
    project_id: int,
    payload: ContractDiscoveryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    documents = list(db.scalars(select(Document).where(
        Document.project_id == project_id,
        Document.id.in_(payload.document_ids),
    )))
    if len(documents) != len(set(payload.document_ids)):
        raise HTTPException(404, "Один или несколько документов проекта не найдены")
    project_contracts = list(db.scalars(select(Contract).where(Contract.project_id == project_id)))
    existing_contract_contents = [
        (contract, _text_for_document(db, db.get(Document, contract.source_document_id)))
        for contract in project_contracts if contract.source_document_id and db.get(Document, contract.source_document_id)
    ]
    linked_by_document = {
        contract.source_document_id: contract for contract in project_contracts if contract.source_document_id is not None
    }
    linked_ids = set(linked_by_document)
    proposals = []
    rejected = []
    content_by_document: dict[int, str] = {}
    for document in documents:
        if document.mime_type and "folder" in document.mime_type:
            continue
        content = _text_for_document(db, document)
        content_by_document[document.id] = content
        proposal = discover_contract_fields(document.name, content)
        if not proposal.pop("is_contract"):
            rejected.append({
                "document_id": document.id, "document_name": document.name,
                "reason": proposal["evidence"][-1] if proposal["evidence"] else "недостаточно признаков договора",
            })
            continue
        proposals.append({
            "document_id": document.id,
            "document_name": document.name,
            "already_linked": document.id in linked_ids,
            "linked_contract_id": linked_by_document.get(document.id).id if document.id in linked_by_document else None,
            "parent_document_id": None,
            "parent_contract_id": None,
            **proposal,
        })

    by_id = {item["document_id"]: item for item in proposals}
    roots = [item for item in proposals if item["contract_kind"] in {"prime_reference", "customer"}]
    for child in proposals:
        linked_contract = linked_by_document.get(child["document_id"])
        existing_parent = _referenced_existing_contract(
            content_by_document.get(child["document_id"], ""), project_contracts,
            linked_contract.id if linked_contract else None,
        )
        party_parent = _party_chain_parent(
            content_by_document.get(child["document_id"], ""), existing_contract_contents,
            linked_contract.id if linked_contract else None,
        )
        inferred_parent = existing_parent or party_parent
        if inferred_parent and child["contract_kind"] == "customer":
            child["contract_kind"] = (
                "revenue_subcontract" if inferred_parent.contract_kind == "prime_reference"
                else "downstream_subcontract"
            )
            child["parent_contract_id"] = inferred_parent.id
            reason = "совпали роли сторон: подрядчик верхнего договора стал заказчиком нижнего" if party_parent is inferred_parent else "найдена явная ссылка в тексте"
            child["evidence"].append(
                f"вышестоящий договор {inferred_parent.number}: {reason}"
            )
        if child["contract_kind"] in {"prime_reference", "customer"}:
            continue
        body = re.sub(r"[^0-9a-zа-яё]+", "", content_by_document.get(child["document_id"], "").casefold())
        referenced = [item for item in proposals if item is not child and len(re.sub(r"\W+", "", item["number"])) >= 4
                      and re.sub(r"[^0-9a-zа-яё]+", "", item["number"].casefold()) in body]
        parent = referenced[0] if referenced else (roots[0] if len(roots) == 1 and not child["parent_contract_id"] else None)
        if parent:
            child["parent_document_id"] = parent["document_id"]
            child["evidence"].append(f"вышестоящий договор: {by_id[parent['document_id']]['number']}")
    return {
        "proposals": proposals, "count": len(proposals), "rejected": rejected,
        "rejected_count": len(rejected), "originals_changed": False,
    }
