from typing import Literal


ContractKind = Literal[
    "prime_reference",
    "customer",
    "revenue_subcontract",
    "downstream_subcontract",
    "supply",
]

CONTRACT_KINDS = {
    "prime_reference",
    "customer",
    "revenue_subcontract",
    "downstream_subcontract",
    "supply",
}
FINANCIAL_CONTRACT_KINDS = {
    "customer",
    "revenue_subcontract",
    "downstream_subcontract",
    "supply",
}
CONTRACT_KIND_LABELS = {
    "prime_reference": "Генподрядный договор — контекст",
    "customer": "Прямой договор с заказчиком",
    "revenue_subcontract": "Наш субподрядный договор с генподрядчиком",
    "downstream_subcontract": "Договор с нашим субподрядчиком",
    "supply": "Договор поставки",
}


def allowed_parent_kinds(kind: str) -> set[str]:
    if kind == "revenue_subcontract":
        return {"prime_reference"}
    if kind in {"downstream_subcontract", "supply"}:
        return {"customer", "revenue_subcontract", "downstream_subcontract"}
    return set()


def is_financial_contract(kind: str) -> bool:
    return kind in FINANCIAL_CONTRACT_KINDS


def cash_flow_direction(kind: str) -> str | None:
    if kind in {"customer", "revenue_subcontract"}:
        return "inflow"
    if kind in {"downstream_subcontract", "supply"}:
        return "outflow"
    return None
