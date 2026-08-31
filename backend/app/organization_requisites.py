import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization_contract import Contract, Organization


FIELD_PATTERNS = {
    "kpp": r"\bКПП\s*[:№]?\s*(\d{9})\b",
    "ogrn": r"\bОГРН\s*[:№]?\s*(\d{13}|\d{15})\b",
    "okpo": r"\bОКПО\s*[:№]?\s*(\d{8,14})\b",
    "okato": r"\bОКАТО\s*[:№]?\s*(\d{8,20})\b",
    "oktmo": r"\bОКТМО\s*[:№]?\s*(\d{8,20})\b",
    "okogu": r"\bОКОГУ\s*[:№]?\s*(\d{7,20})\b",
    "settlement_account": r"(?:р\s*/\s*с|расч[её]тный\s+сч[её]т)\s*[:№]?\s*(\d{20})\b",
    "correspondent_account": r"(?:к\s*/\s*с|корреспондентский\s+сч[её]т)\s*[:№]?\s*(\d{20})\b",
    "bik": r"\bБИК\s*[:№]?\s*(\d{9})\b",
    "email": r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b",
}


def extract_organization_requisites(text: str) -> list[dict[str, str]]:
    """Extract explainable organization candidates; never changes source text."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"\bИНН\s*[:№]?\s*(\d{10}|\d{12})\b", text, re.IGNORECASE):
        inn = match.group(1)
        if inn in seen:
            continue
        seen.add(inn)
        start, end = max(0, match.start() - 2500), min(len(text), match.end() + 3500)
        window = text[start:end]
        profile: dict[str, str] = {"inn": inn}
        legal_names = list(re.finditer(
            r"\b(?:ООО|АО|ПАО|ЗАО|ОАО)\s*[«\"]?[A-ZА-ЯЁ0-9][^\n,;]{1,100}[»\"]?",
            window, re.IGNORECASE,
        ))
        if legal_names:
            nearest = min(legal_names, key=lambda item: abs((start + item.start()) - match.start()))
            profile["legal_name"] = re.sub(r"\s+", " ", nearest.group(0)).strip(' .,:;')
            profile["name"] = profile["legal_name"]
        for field, pattern in FIELD_PATTERNS.items():
            found = re.search(pattern, window, re.IGNORECASE)
            if found:
                profile[field] = found.group(1).strip()
        results.append(profile)
    return results


def remember_contract_organizations(db: Session, contract: Contract, content: str, source_document_id: int) -> list[Organization]:
    """Remember parties by INN without overwriting user-confirmed requisites."""
    remembered: list[Organization] = []
    counterparty_key = re.sub(r"\W+", "", (contract.counterparty or "").casefold())
    for profile in extract_organization_requisites(content):
        organization = db.scalar(select(Organization).where(Organization.inn == profile["inn"]))
        if organization is None:
            organization = Organization(name=profile.get("name") or f"Организация ИНН {profile['inn']}", inn=profile["inn"])
            db.add(organization)
            db.flush()
        if organization.requisites_status != "confirmed":
            for field, value in profile.items():
                if field != "name" and value and not getattr(organization, field, None):
                    setattr(organization, field, value)
            organization.source_document_id = organization.source_document_id or source_document_id
            organization.requisites_status = "extracted"
        legal_key = re.sub(r"\W+", "", (organization.legal_name or organization.name).casefold())
        if counterparty_key and (counterparty_key in legal_key or legal_key in counterparty_key):
            contract.counterparty_organization_id = organization.id
        remembered.append(organization)
    return remembered
