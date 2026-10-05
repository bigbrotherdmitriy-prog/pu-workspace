"""Server-owned VAT provenance; monetary values always remain gross amounts."""
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from app.models.organization_contract import Contract


def contract_vat_snapshot(contract: "Contract | None") -> dict | None:
    """Capture the current explicit decision, including unspecified and zero rate."""
    if contract is None:
        return None
    return {
        "schema_version": 1,
        "mode": contract.vat_mode,
        "rate": format(Decimal(str(contract.vat_rate)), ".2f") if contract.vat_rate is not None else None,
        "source_contract_id": contract.id,
        "source_contract_record_version": contract.record_version,
    }


def assert_proposed_vat_current(contract: "Contract | None", snapshot: dict | None) -> None:
    """A known proposal must not silently adopt changed tax conditions.

    SQL NULL is retained for historical/unlinked records; this check does not
    turn unknown provenance into a claim about the current contract.
    """
    if snapshot is None:
        return
    current = contract_vat_snapshot(contract)
    tax_fields = ("schema_version", "mode", "rate", "source_contract_id")
    if current is None or any(snapshot.get(field) != current[field] for field in tax_fields):
        raise HTTPException(409, "VAT_SNAPSHOT_STALE: условия НДС изменились; явно обновите предложение")
