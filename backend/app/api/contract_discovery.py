from __future__ import annotations

import hashlib
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import SessionLocal, get_db
from app.jobs.queue import enqueue, retry, update_cooperative_progress
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.job import BackgroundJob
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.user import User


router = APIRouter(tags=["contracts"])


class ContractDiscoveryRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)


class ContractDiscoveryJobsRequest(BaseModel):
    document_ids: list[int] | None = Field(default=None, min_length=1, max_length=5_000)


CONTRACT_DISCOVERY_BATCH_SIZE = 200


_NUMBER_RE = re.compile(
    r"(?:договор|контракт)\s*(?:(?:поставки|подряда|субподряда|оказания\s+услуг)\s*)?"
    r"(?:№|N|номер)?\s*[:№N-]?\s*"
    r"([A-ZА-ЯЁ0-9][A-ZА-ЯЁa-zа-яё0-9./_-]{2,80})",
    re.IGNORECASE,
)
_FILENAME_CONTRACT_NUMBER_RE = re.compile(
    r"^(?P<number>[A-ZА-ЯЁ0-9]{1,12}(?:[-_/][A-ZА-ЯЁ0-9]{2,24}){2,8})$",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r"\b((?:общество\s+с\s+ограниченной\s+ответственностью|"
    r"индивидуальн(?:ый|ого)\s+предпринимател(?:ь|я)|ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|ГУП|МУП)\b"
    r"\s*(?:[«\"'][^»\"'\n]{2,100}[»\"']|[^\n,;]{2,100}?))"
    r"(?=\s*(?:,|именуем|в лице|$))",
    re.IGNORECASE,
)

_OWN_HEADING_RE = re.compile(
    r"(?:^|[\r\n]|[.!?]\s+)(?:государственн(?:ый|ого)\s+)?"
    r"(?:договор|контракт)(?:\s+(?:подряда|субподряда|поставки|оказания\s+услуг))?\b",
    re.IGNORECASE,
)
_CONCLUSION_RE = re.compile(r"заключил[иао]?\s+(?:между\s+собой\s+)?настоящ(?:ий|его)\s+(?:договор|контракт)", re.IGNORECASE)
_ROLE_RE = re.compile(r"\b(заказчик|подрядчик|покупатель|поставщик|исполнитель)\b", re.IGNORECASE)
_DATE_NEAR_HEADING_RE = re.compile(r"(?:договор|контракт).{0,180}\bот\s+\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", re.IGNORECASE)
_REFERENCE_ONLY_RE = re.compile(r"\b(?:к|по|во\s+исполнение|в\s+соответствии\s+с)\s+(?:настоящ(?:ему|его)\s+)?(?:договору|договором|договора|контракту|контрактом|контракта)\s*(?:№|N)", re.IGNORECASE)
_NEGATIVE_DOCUMENT_RE = re.compile(
    r"(?:^|\W)(?:приложени|спецификац|дополнени|график|ведомост|смет|техническ.*задани|"
    r"акт|сч[её]т|накладн|письм|протокол|заявк|доверенност|пропуск)",
    re.IGNORECASE,
)
_LEGAL_SECTIONS = (
    ("предмет", re.compile(r"\bпредмет\s+(?:договора|контракта)\b", re.IGNORECASE)),
    ("цена/расчёты", re.compile(r"\b(?:цена|стоимость)\s+(?:договора|контракта)|\bпорядок\s+расч[её]тов\b", re.IGNORECASE)),
    ("права и обязанности", re.compile(r"\bправа\s+и\s+обязанности\b", re.IGNORECASE)),
    ("срок", re.compile(r"\b(?:срок\s+действия|сроки?\s+(?:выполнения|поставки|оказания))\b", re.IGNORECASE)),
    ("ответственность", re.compile(r"\bответственност[ьи]\s+сторон\b", re.IGNORECASE)),
    ("споры", re.compile(r"\b(?:разрешение|порядок\s+разрешения)\s+споров\b", re.IGNORECASE)),
    ("реквизиты", re.compile(r"\b(?:адреса\s+и\s+)?реквизиты\s+(?:и\s+подписи\s+)?сторон\b", re.IGNORECASE)),
)


# OCR of scanned contracts splits words and numbers with stray spaces
# («Граждан ин», «Юри я», «№ 01/ 21 11 -2 6»); owner rule 15.10.2026:
# the system fills the card itself, so these are repaired, not left empty.
_SPACED_NUMBER_RE = re.compile(r"(?:договор|контракт)[^№\n]{0,40}№\s*(?P<tail>[^\n]{1,80})", re.IGNORECASE)
_PERSON_RE = re.compile(
    r"граждан\s*(?:ин|ка)\s*(?:РФ|Российской\s+Федерации)?\s*,?\s*"
    r"(?P<name>[А-ЯЁ][а-яё]+(?:\s+[а-яё]{1,2}(?=\s|,))?\s+[А-ЯЁ][а-яё]+(?:\s+[а-яё]{1,2}(?=\s|,))?"
    r"\s+[А-ЯЁ][а-яё]+(?:\s+[а-яё]{1,2}(?=\s|,))?)",
    re.IGNORECASE,
)


_MONTH_RE = re.compile(
    r"^(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
    re.IGNORECASE,
)


def _spaced_contract_number(heading: str) -> str | None:
    """«№ 6 - FIL /730-02- 2026 г.» → «6-FIL/730-02-2026»; «№ 01/ 21 11 -2 6 возмездного» → «01/2111-26»."""
    match = _SPACED_NUMBER_RE.search(heading)
    if not match:
        return None
    parts: list[str] = []
    tokens = match.group("tail").split()
    for index, token in enumerate(tokens):
        if token.startswith(("«", "\"")):  # quoted OCR fragment, not part of the number
            break
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if re.fullmatch(r"\d{1,2}", token) and parts and _MONTH_RE.match(following):
            break  # «№ 52 02 февраля» -- the day of the date, not the number
        if re.search(r"[а-яё]", token):  # first lowercase Cyrillic word (г., от, возмездного) ends the number
            break
        if _MONTH_RE.match(token):  # day-of-month followed by a month name, not the number
            break
        if not re.fullmatch(r"[A-ZА-ЯЁ0-9/._\-]+", token, re.IGNORECASE):
            break
        parts.append(token)
    number = "".join(parts).strip("-./_")
    return number if len(number) >= 2 and any(ch.isdigit() for ch in number) else None


def _join_ocr_fragments(value: str) -> str:
    """Glue 1–2 letter lowercase OCR fragments back to the previous word: «Юри я» → «Юрия»."""
    # Only word endings, never real short words such as «с», «и», «в», «по».
    return re.sub(r"(?<=[а-яё])\s+(я|ы|й|е|ь|ый|ий|ая|ое|ой|ич|ов|ев)(?=\s|$|[,.;])", r"\1", value)


def _nominative_person(name: str) -> str:
    words = _join_ocr_fragments(" ".join(name.split())).split()
    if len(words) != 3:
        return " ".join(words)
    surname, first, patronymic = words
    if patronymic.endswith("ича"):  # genitive male → nominative
        patronymic = patronymic[:-1]
        if first.endswith("ия"):
            first = first[:-2] + "ий"
        elif first.endswith("я") and len(first) > 3:
            first = first[:-1] + "ь"
        elif first.endswith("а") and len(first) > 3:
            first = first[:-1]
        if surname.endswith(("ова", "ева", "ина", "ына")):
            surname = surname[:-1]
    elif patronymic.endswith(("овны", "евны")):
        patronymic = patronymic[:-1] + "а"
        if first.endswith("ы"):
            first = first[:-1] + "а"
        elif first.endswith("и") and len(first) > 3:
            first = first[:-1] + "я"
        if surname.endswith(("овой", "евой", "иной")):
            surname = surname[:-2] + "а"
    return f"{surname} {first} {patronymic}"


def _normalized_party(value: str) -> str:
    value = _join_ocr_fragments(" ".join(value.split())).strip(" .,:;")
    organization = re.match(r"обществ[оа]\s+с\s+ограниченной\s+ответственностью\s*(.+)$", value, re.IGNORECASE)
    if organization:
        return f"ООО {organization.group(1).strip()}"
    entrepreneur = re.match(r"индивидуальн(?:ый|ого)\s+предпринимател(?:ь|я)\s+(.+)$", value, re.IGNORECASE)
    if entrepreneur:
        return f"ИП {_nominative_person(entrepreneur.group(1))}"
    return value


def _discovered_counterparty(text: str, kind: str, own_names: tuple[str, ...] = ()) -> tuple[str | None, str]:
    """Resolve an external provider by its legal role, never by document order."""
    body = text[:15_000]
    matches = list(_COMPANY_RE.finditer(body))
    companies: dict[str, str] = {}
    providers: dict[str, dict[str, tuple[str, str]]] = {
        "поставщик": {}, "подрядчик": {}, "исполнитель": {},
    }
    customers: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        company = " ".join(match.group(1).split()).strip(" .,:;")
        key = _organization_key(company)
        companies.setdefault(key, company)
        # A role belongs only to the current organization, not the next party.
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        tail = body[match.end():min(end, match.end() + 800)]
        role = re.search(
            r"именуем\w*\s+(?:в\s+дальнейшем\s+)?[«\"']?"
            r"(поставщик|покупатель|заказчик|подрядчик|исполнитель)\b", tail, re.IGNORECASE,
        )
        normalized_role = role.group(1).casefold() if role else None
        if normalized_role in providers:
            providers[normalized_role][key] = (company, body[match.start():match.end() + role.end()])
        elif normalized_role in {"заказчик", "покупатель"}:
            customers[key] = (company, body[match.start():match.end() + role.end()])
    for person in _PERSON_RE.finditer(body):
        tail = body[person.end():person.end() + 300]
        role = re.search(
            r"именуем\w*(?:\s+\w{1,3})?\s+(?:в\s+дальнейшем\s+)?[«\"']?(исполнитель|подрядчик|поставщик)\b",
            tail, re.IGNORECASE,
        )
        if not role:
            continue
        name = _nominative_person(person.group("name"))
        nearby = body[max(0, person.start() - 600):person.end() + 300].casefold()
        if "самозанят" in nearby or "профессиональный доход" in nearby:
            name = f"{name} (самозанятый)"
        providers[role.group(1).casefold()][name.casefold()] = (name, body[person.start():person.end() + role.end()])
    own_keys = {_organization_key(name) for name in own_names if name}
    if own_keys & set(customers):
        # Our company orders the work: the single provider is the counterparty.
        other_providers = {key: value for role in providers.values() for key, value in role.items()
                           if key not in own_keys}
        if len(other_providers) == 1:
            company, quote = next(iter(other_providers.values()))
            return _normalized_party(company), f"контрагент предложен по роли исполнителя/подрядчика: {quote}"
    desired_roles = {
        "supply": ("поставщик",),
        "downstream_subcontract": ("подрядчик", "исполнитель"),
    }.get(kind, ())
    if desired_roles:
        matches_by_role = {
            key: value
            for role_name in desired_roles
            for key, value in providers[role_name].items()
        }
        if len(matches_by_role) == 1:
            company, quote = next(iter(matches_by_role.values()))
            role_label = "поставщика" if kind == "supply" else "подрядчика/исполнителя"
            return _normalized_party(company), f"контрагент предложен по роли {role_label}: {quote}"
        expected = "поставщик" if kind == "supply" else "подрядчик/исполнитель"
        return None, f"{expected} не определён однозначно; подтвердите контрагента вручную"
    # The customer role is the counterparty only when the project's own company is
    # the contractor in this contract (we cannot tell sides apart otherwise).
    provider_rows = {key: value for role in providers.values() for key, value in role.items()}
    other_customers = {key: value for key, value in customers.items() if key not in own_keys}
    if own_keys & set(provider_rows) and len(other_customers) == 1:
        company, quote = next(iter(other_customers.values()))
        return _normalized_party(company), f"контрагент предложен по роли заказчика: {quote}"
    if not customers and len(provider_rows) == 1:
        company, quote = next(iter(provider_rows.values()))
        return _normalized_party(company), f"контрагент предложен по роли исполнителя: {quote}"
    if len(companies) == 1:
        return _normalized_party(next(iter(companies.values()))), "найдена одна организация; подтвердите контрагента"
    return None, "стороны не определены однозначно; подтвердите контрагента вручную"


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
    r"(?P<org>(?:(?:общество\s+с\s+ограниченной\s+ответственностью)|"
    r"(?:индивидуальн(?:ый|ого)\s+предпринимател(?:ь|я))|ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|ГУП|МУП)"
    r"\s+.{1,220}?)\s*,?\s*именуем\w*\s+(?:в\s+дальнейшем\s+)?[«\"']?"
    r"(?P<role>заказчик|подрядчик|покупатель|поставщик|исполнитель)[»\"']?",
    re.IGNORECASE,
)


def _organization_key(value: str) -> str:
    quoted = re.findall(r"[«\"]([^»\"]{2,100})[»\"]", value)
    candidate = min(quoted, key=len) if quoted else value
    candidate = re.sub(
        r"\b(?:общество\s+с\s+ограниченной\s+ответственностью|"
        r"индивидуальн(?:ый|ого)\s+предпринимател(?:ь|я)|ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|ГУП|МУП)\b",
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


def _short_contract_title(content: str, fallback: str) -> str:
    """Build a compact, reviewable name from the contract subject clause."""
    compact = " ".join((content or "").split())[:20_000]
    subject_match = re.search(
        r"предмет\s+(?:договора|контракта)\s*(?:\d+(?:\.\d+)*[.)]?\s*)?(.{20,1800}?)"
        r"(?=\s+\d+\.\d+[.)]?\s|\s+2[.)]\s|$)",
        compact, re.IGNORECASE,
    )
    subject = subject_match.group(1) if subject_match else ""
    if not subject:
        obligation = re.search(
            r"(?:принимает\s+на\s+себя\s+обязательств\w*|обязуется)\s+(.{20,900}?)"
            r"(?=\s+заказчик\s+обязуется|\s+в\s+соответствии\s+с|[.;])",
            compact, re.IGNORECASE,
        )
        subject = obligation.group(1) if obligation else ""
    subject = re.sub(r"\([^)]{0,180}\)", " ", subject)
    subject = re.sub(
        r"^.*?(?:выполнени[еяю]\s+работ\s+по|оказани[еяю]\s+услуг\s+по|поставк[еи]\s+|поставить\s+|выполнить\s+)",
        "", subject, flags=re.IGNORECASE,
    )
    subject = re.split(
        r"\s+(?:в\s+соответствии\s+с|а\s+заказчик\s+обязуется|заказчик\s+обязуется|по\s+адресу)\b",
        subject, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    subject = re.sub(r"\s+", " ", subject).strip(" .,:;-–—")
    if len(subject) < 12:
        return fallback
    if len(subject) > 180:
        subject = subject[:181].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return subject[0].upper() + subject[1:]


def _party_chain_parent(content: str, contract_contents: list[tuple[Contract, str]], excluded_id: int | None = None) -> Contract | None:
    child_parties = _contract_parties(content)
    child_demand_parties = {
        child_parties[role] for role in ("заказчик", "покупатель") if child_parties.get(role)
    }
    if len(child_demand_parties) != 1:
        return None
    child_customer = next(iter(child_demand_parties))
    matches = []
    for contract, parent_content in contract_contents:
        if contract.id == excluded_id:
            continue
        parent_parties = _contract_parties(parent_content)
        parent_provider_parties = {
            parent_parties[role]
            for role in ("подрядчик", "исполнитель", "поставщик")
            if parent_parties.get(role)
        }
        if child_customer in parent_provider_parties:
            matches.append(contract)
    return matches[0] if len(matches) == 1 else None


def _own_is_customer(own_names: tuple[str, ...], content: str) -> bool:
    """True when our organization is named as the customer/buyer/general contractor."""
    if not own_names:
        return False
    own_keys = {_organization_key(name) for name in own_names if name}
    if not own_keys:
        return False
    compact = " ".join((content or "").split())[:15_000].casefold()
    for key in own_keys:
        if not key:
            continue
        start = 0
        while True:
            index = compact.find(key, start)
            if index < 0:
                break
            tail = compact[index + len(key):index + len(key) + 300]
            if re.search(
                r"именуем\w*\s+(?:в\s+дальнейшем\s+)?[«\"']?"
                r"(?:заказчик|покупатель|генподрядчик)\b",
                tail, re.IGNORECASE,
            ):
                return True
            start = index + len(key)
    return False


def _common_subject_words(first: str, second: str) -> bool:
    """True when two contract subjects share a significant common word (≥5 letters)."""
    words = {word for word in re.findall(r"[A-Za-zА-Яа-яЁё]{5,}", (first or "").casefold())}
    other = {word for word in re.findall(r"[A-Za-zА-Яа-яЁё]{5,}", (second or "").casefold())}
    return bool(words & other)


def _own_customer_parent(content: str, own_names: tuple[str, ...],
                         project_contracts: list[Contract]) -> Contract | None:
    """Choose the single head contract where our company is the contractor."""
    candidates = [
        contract for contract in project_contracts
        if not getattr(contract, "archived", False)
        and contract.contract_kind in {"prime_reference", "customer"}
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        subject = _short_contract_title(content, "") or ""
        matched = [contract for contract in candidates if _common_subject_words(contract.title or "", subject)]
        if len(matched) == 1:
            return matched[0]
    return None


def discover_contract_fields(name: str, content: str, own_names: tuple[str, ...] = ()) -> dict:
    """Return a reviewable proposal. It never creates or links records."""
    raw = content or ""
    text = " ".join(raw.split())
    heading_source = f"{Path(name).stem}\n{raw[:1_800]}"
    lowered = f"{name}\n{text[:20_000]}".casefold()
    own_heading = bool(_OWN_HEADING_RE.search(heading_source))
    number_match = _NUMBER_RE.search(heading_source) if own_heading else None
    if number_match:
        candidate = number_match.group(1).strip(" .,:;№")
        # OCR frequently turns headings such as "договором" into a bogus number "ом".
        # A usable automatic number must contain a digit; otherwise the filename is safer.
        if not any(character.isdigit() for character in candidate):
            number_match = None
    spaced_number = _spaced_contract_number(heading_source) if own_heading else None
    if spaced_number and (not number_match or len(spaced_number) > len(number_match.group(1).strip(" .,:;№"))):
        number_match = None
    fallback_number = Path(name).stem.strip()[:255]
    filename_number_match = _FILENAME_CONTRACT_NUMBER_RE.fullmatch(fallback_number.replace(" ", ""))
    filename_number = filename_number_match.group("number") if filename_number_match else None
    number = (number_match.group(1).strip(" .,:;№") if number_match
              else spaced_number or filename_number or fallback_number) or "Без номера"
    companies = []
    for value in _COMPANY_RE.findall(text[:15_000]):
        normalized = " ".join(value.split()).strip(" .,:;")
        if normalized.casefold() not in {item.casefold() for item in companies}:
            companies.append(normalized)

    negative_document = bool(_NEGATIVE_DOCUMENT_RE.search(Path(name).stem))
    reference_only = bool(_REFERENCE_ONLY_RE.search(heading_source)) and not own_heading
    roles = {match.casefold() for match in _ROLE_RE.findall(text[:8_000])}
    paired_roles = bool(
        {"заказчик", "подрядчик"}.issubset(roles)
        or {"покупатель", "поставщик"}.issubset(roles)
        or {"заказчик", "исполнитель"}.issubset(roles)
    )
    conclusion = bool(_CONCLUSION_RE.search(text[:8_000]))
    date_near_heading = bool(_DATE_NEAR_HEADING_RE.search(heading_source))
    legal_sections = [label for label, pattern in _LEGAL_SECTIONS if pattern.search(text[:20_000])]
    company_pair = len(companies) >= 2
    corroborating = date_near_heading or paired_roles or conclusion or bool(legal_sections) or company_pair or len(roles) >= 1

    heading = f"{name}\n{text[:1_800]}".casefold()
    if not negative_document and (("государственн" in heading and "контракт" in heading) or "генподряд" in heading or re.search(r"(?:^|\W)гк[-_№\s]", name.casefold())):
        kind, kind_reason = "prime_reference", "найден государственный/генподрядный контекст"
    elif "поставк" in lowered or "поставщик" in lowered:
        kind, kind_reason = "supply", "найдены признаки договора поставки"
    elif "субсубподряд" in heading or "субподрядчик" in heading or "субподряд" in heading:
        kind, kind_reason = "downstream_subcontract", "найдены признаки субподряда"
    else:
        kind, kind_reason = "customer", "роль сторон требует проверки пользователя"

    identity_with_number = own_heading and bool(number_match) and corroborating
    structured_scan = bool(filename_number) and paired_roles and len(legal_sections) >= 2
    legal_document = own_heading and conclusion and paired_roles and len(legal_sections) >= 2
    content_proves_standalone_contract = own_heading and bool(number_match) and bool(legal_sections)
    blocked_by_filename = negative_document and not content_proves_standalone_contract
    is_contract = not blocked_by_filename and not reference_only and (
        identity_with_number or structured_scan or legal_document
    )
    confidence = min(0.95, 0.28 + (0.22 if own_heading else 0) + (0.18 if number_match else 0)
                     + (0.12 if filename_number else 0) + (0.08 if paired_roles else 0)
                     + (0.06 if conclusion else 0) + min(0.12, len(legal_sections) * 0.03)
                     + (0.03 if date_near_heading else 0))
    evidence = [kind_reason]
    if own_heading:
        evidence.append("собственный заголовок договора найден")
    if number_match:
        evidence.append("номер найден рядом с заголовком")
    elif filename_number:
        evidence.append("структурированный номер договора найден в имени файла")
    if date_near_heading:
        evidence.append("дата найдена рядом с заголовком")
    if paired_roles:
        evidence.append("найдена пара юридических ролей сторон")
    if conclusion:
        evidence.append("найдена формулировка заключения настоящего договора")
    if legal_sections:
        evidence.append(f"юридические разделы ({len(legal_sections)}): {', '.join(legal_sections)}")
    if negative_document and content_proves_standalone_contract:
        evidence.append("содержимое подтверждает самостоятельный договор вопреки имени файла")
    elif negative_document:
        evidence.append("файл похож на приложение, а не на самостоятельный договор")
        evidence.append("исключён: заголовок относится к акту, счёту, письму или приложению")
    elif reference_only:
        evidence.append("исключён: найдена только ссылка на другой договор")
    elif not is_contract:
        evidence.append("исключён: недостаточно независимых признаков самостоятельного договора")
    counterparty, party_evidence = _discovered_counterparty(text, kind, own_names)
    evidence.insert(1, party_evidence)
    return {
        "number": number,
        "title": _short_contract_title(content, fallback_number),
        "counterparty": counterparty,
        "contract_kind": kind,
        "confidence": round(confidence, 2),
        "is_contract": is_contract,
        "evidence": evidence,
    }


def _discover_documents(db: Session, project_id: int, documents: list[Document],
                        pinned_contents: dict[int, str] | None = None) -> dict:
    """Classify documents and build review proposals without mutating business records."""
    project_contracts = list(db.scalars(select(Contract).where(Contract.project_id == project_id)))
    existing_contract_contents = [
        (contract, _text_for_document(db, db.get(Document, contract.source_document_id)))
        for contract in project_contracts if contract.source_document_id and db.get(Document, contract.source_document_id)
    ]
    linked_by_document = {
        contract.source_document_id: contract for contract in project_contracts if contract.source_document_id is not None
    }
    linked_ids = set(linked_by_document)
    project = db.get(Project, project_id)
    own_organization = db.get(Organization, project.organization_id) if project is not None else None
    own_names = tuple(filter(None, (
        own_organization.name if own_organization else None,
        own_organization.legal_name if own_organization else None,
    )))
    proposals = []
    rejected = []
    content_by_document: dict[int, str] = {}
    for document in documents:
        if document.mime_type and "folder" in document.mime_type:
            continue
        content = pinned_contents.get(document.id, "") if pinned_contents is not None else _text_for_document(db, document)
        content_by_document[document.id] = content
        proposal = discover_contract_fields(document.name, content, own_names)
        if not proposal.pop("is_contract"):
            rejected.append({
                "document_id": document.id, "document_name": document.name,
                "reason": proposal["evidence"][-1] if proposal["evidence"] else "недостаточно признаков договора",
                "already_linked": document.id in linked_ids,
                "linked_contract_id": linked_by_document.get(document.id).id if document.id in linked_by_document else None,
                "parent_document_id": None, "parent_contract_id": None,
                **proposal,
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
        if inferred_parent and child["contract_kind"] != "prime_reference":
            if child["contract_kind"] == "customer":
                child["contract_kind"] = (
                    "revenue_subcontract" if inferred_parent.contract_kind == "prime_reference"
                    else "downstream_subcontract"
                )
            child["parent_contract_id"] = inferred_parent.id
            reason = (
                "совпали роли сторон: исполнитель верхнего договора стал заказчиком/покупателем нижнего"
                if party_parent is inferred_parent else "найдена явная ссылка в тексте"
            )
            child["evidence"].append(
                f"вышестоящий договор {inferred_parent.number}: {reason}"
            )
        if (not child["parent_contract_id"]
                and child["contract_kind"] not in {"prime_reference", "customer"}
                and _own_is_customer(own_names, content_by_document.get(child["document_id"], ""))):
            own_parent = _own_customer_parent(
                content_by_document.get(child["document_id"], ""), own_names, project_contracts,
            )
            if own_parent is not None:
                child["parent_contract_id"] = own_parent.id
                child["evidence"].append(
                    f"вышестоящий договор {own_parent.number}: наша компания заказчик в этом договоре "
                    "и подрядчик в головном"
                )
        if not child.get("counterparty"):
            counterparty, party_evidence = _discovered_counterparty(
                content_by_document.get(child["document_id"], ""), child["contract_kind"], own_names,
            )
            if counterparty:
                child["counterparty"] = counterparty
                child["evidence"].append(party_evidence)
        if child["contract_kind"] in {"prime_reference", "customer"}:
            continue
        if child["parent_contract_id"]:
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
    return _discover_documents(db, project_id, documents)


def _latest_version_pins(db: Session, document_ids: list[int]) -> list[dict[str, int]]:
    pins = []
    for document_id in document_ids:
        version = db.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
        ).order_by(DocumentVersion.version_number.desc()).limit(1))
        pins.append({"document_id": document_id, "version_id": version.id if version else 0})
    return pins


def _contract_discovery_batches(pins: list[dict[str, int]]) -> list[list[dict[str, int]]]:
    return [pins[offset:offset + CONTRACT_DISCOVERY_BATCH_SIZE]
            for offset in range(0, len(pins), CONTRACT_DISCOVERY_BATCH_SIZE)]


@router.post("/projects/{project_id}/contracts/discovery-jobs")
def start_contract_discovery_jobs(
    project_id: int,
    payload: ContractDiscoveryJobsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "editor")
    query = select(Document).where(Document.project_id == project_id)
    if payload.document_ids is not None:
        query = query.where(Document.id.in_(payload.document_ids))
    documents = [row for row in db.scalars(query.order_by(Document.id))
                 if not (row.mime_type and "folder" in row.mime_type)]
    if payload.document_ids is not None and len(documents) != len(set(payload.document_ids)):
        raise HTTPException(404, "Один или несколько документов проекта не найдены")
    if not documents:
        return {"jobs": [], "total": 0, "batch_size": CONTRACT_DISCOVERY_BATCH_SIZE}

    pins = _latest_version_pins(db, [row.id for row in documents])
    jobs = []
    for batch in _contract_discovery_batches(pins):
        fingerprint = hashlib.sha256(
            f"{project_id}:".encode() + ",".join(
                f"{item['document_id']}:{item['version_id']}" for item in batch
            ).encode()
        ).hexdigest()
        job = enqueue(db, "contracts.discover_batch", {
            "project_id": project_id, "document_versions": batch,
        }, idempotency_key=f"contracts-discover:{project_id}:{fingerprint}")
        if job.status == "failed":
            retry(db, job.id)
            db.refresh(job)
        elif job.status == "dead_letter":
            retry(db, job.id, redrive=True)
            db.refresh(job)
        if not (job.payload or {}).get("job_id"):
            job.payload = {**dict(job.payload or {}), "job_id": job.id}
            db.commit()
        jobs.append({"job_id": job.id, "status": job.status, "count": len(batch)})
    return {"jobs": jobs, "total": len(pins), "batch_size": CONTRACT_DISCOVERY_BATCH_SIZE}


@router.get("/projects/{project_id}/contracts/discovery-jobs/{job_id}")
def get_contract_discovery_job(
    project_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    job = db.get(BackgroundJob, job_id)
    if (job is None or job.kind != "contracts.discover_batch"
            or int((job.payload or {}).get("project_id", -1)) != project_id):
        raise HTTPException(404, "Contract discovery job not found")
    return {
        "job_id": job.id, "status": job.status, "progress": job.progress,
        "attempts": job.attempts, "result": job.result,
        "error": job.last_error if job.status in {"failed", "dead_letter"} else None,
    }


def _discover_contract_batch_job(payload: dict) -> dict:
    project_id = int(payload["project_id"])
    pins = [
        {"document_id": int(item["document_id"]), "version_id": int(item["version_id"])}
        for item in payload.get("document_versions", [])
    ]
    if len(pins) > CONTRACT_DISCOVERY_BATCH_SIZE:
        raise ValueError("Contract discovery batch exceeds 200 documents")
    with SessionLocal() as db:
        document_ids = [item["document_id"] for item in pins]
        version_ids = [item["version_id"] for item in pins if item["version_id"]]
        documents = list(db.scalars(select(Document).where(
            Document.project_id == project_id, Document.id.in_(document_ids),
        )))
        versions = list(db.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(version_ids)))) if version_ids else []
        version_by_id = {row.id: row for row in versions}
        pinned_contents = {
            item["document_id"]: version_by_id[item["version_id"]].content if item["version_id"] else ""
            for item in pins if not item["version_id"] or item["version_id"] in version_by_id
        }
        if len(documents) != len(document_ids) or len(pinned_contents) != len(pins):
            raise ValueError("Pinned contract discovery source changed or disappeared")
        job_id = payload.get("job_id")
        if job_id is not None:
            update_cooperative_progress(db, int(job_id), 20, {
                "completed": 0, "total": len(documents), "percent": 20,
            })
        result = _discover_documents(db, project_id, documents, pinned_contents)
        if job_id is not None:
            update_cooperative_progress(db, int(job_id), 95, {
                "completed": len(documents), "total": len(documents), "percent": 95,
            })
        return {**result, "processed": len(documents)}
