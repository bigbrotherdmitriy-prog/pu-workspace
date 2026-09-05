from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.mvp4.finance_guards import exact_decimal


COMMAND_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$"


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command_key: str = Field(min_length=8, max_length=120, pattern=COMMAND_KEY_PATTERN)


class EvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_id: UUID
    evidence_revision: Literal[1] = 1
    source_version_id: UUID
    document_version_id: int = Field(gt=0)


class CreateSupplyRequest(StrictCommand):
    organization_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    contract_id: int = Field(gt=0)
    schedule_baseline_id: int = Field(gt=0)
    schedule_baseline_version: int = Field(gt=0)
    schedule_item_id: int = Field(gt=0)
    task_id: int = Field(gt=0)
    evidence: EvidenceLink
    title: str = Field(min_length=2, max_length=500)
    supplier: str = Field(min_length=2, max_length=500)
    requested_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    unit: str = Field(min_length=1, max_length=30)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")

    @field_validator("title", "supplier", "unit")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("blank value")
        return stripped

    @field_validator("requested_quantity")
    @classmethod
    def exact_quantity(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 3, reason="quantity_precision")

    @field_validator("unit_price")
    @classmethod
    def exact_money(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 2, reason="money_precision")


class VersionedCommand(StrictCommand):
    expected_version: int = Field(gt=0)


class ReviewSupplyRequest(VersionedCommand):
    decision: Literal["confirm", "reject"]
    corrected_title: str | None = Field(default=None, min_length=2, max_length=500)
    corrected_supplier: str | None = Field(default=None, min_length=2, max_length=500)
    corrected_quantity: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=3)
    corrected_unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)

    @field_validator("corrected_quantity")
    @classmethod
    def exact_quantity(cls, value: Decimal | None):
        return exact_decimal(value, 3, reason="quantity_precision") if value is not None else None

    @field_validator("corrected_unit_price")
    @classmethod
    def exact_money(cls, value: Decimal | None):
        return exact_decimal(value, 2, reason="money_precision") if value is not None else None

    @model_validator(mode="after")
    def reject_has_no_corrections(self):
        if self.decision == "reject" and any(
            value is not None
            for value in (
                self.corrected_title,
                self.corrected_supplier,
                self.corrected_quantity,
                self.corrected_unit_price,
            )
        ):
            raise ValueError("rejected review cannot mutate request")
        return self


class PrepareOrder(VersionedCommand):
    ordered_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    order_reference: str = Field(min_length=2, max_length=200)

    @field_validator("ordered_quantity")
    @classmethod
    def exact_quantity(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 3, reason="quantity_precision")


class RecordOrder(VersionedCommand):
    evidence: EvidenceLink


class RecordDelivery(VersionedCommand):
    delivered_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    evidence: EvidenceLink
    discrepancy_code: Literal["quantity", "quality", "damage", "documents", "other"] | None = None
    discrepancy_note: str | None = Field(default=None, min_length=3, max_length=1000)

    @field_validator("delivered_quantity")
    @classmethod
    def exact_quantity(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 3, reason="quantity_precision")

    @model_validator(mode="after")
    def discrepancy_is_explicit(self):
        if (self.discrepancy_code is None) != (self.discrepancy_note is None):
            raise ValueError("discrepancy code and note must be supplied together")
        return self


class ProposeAcceptanceAct(VersionedCommand):
    accepted_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    act_number: str = Field(min_length=1, max_length=200)
    evidence: EvidenceLink

    @field_validator("accepted_quantity")
    @classmethod
    def exact_quantity(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 3, reason="quantity_precision")


class ResolveDiscrepancy(VersionedCommand):
    decision: Literal["accept_recorded_quantity", "return_to_delivery"]


class CreateDdsProposal(VersionedCommand):
    """Human-requested proposal only; it never confirms or executes payment."""

    contract_id: int = Field(gt=0)
    schedule_item_id: int = Field(gt=0)
    budget_line_id: int = Field(gt=0)
    planned_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence_assessment_version: int = Field(gt=0)
    evidence: EvidenceLink

    @field_validator("amount")
    @classmethod
    def exact_money(cls, value: Decimal) -> Decimal:
        return exact_decimal(value, 2, reason="money_precision")


class DdsProposalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supply_case_id: int
    cash_flow_id: int
    supply_record_version: int
    status: Literal["proposed"] = "proposed"
    requires_human_confirmation: Literal[True] = True
    payment_created: Literal[False] = False
    already_applied: bool = False


class SupplyMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supply_case_id: int
    status: str
    record_version: int
    already_applied: bool = False
    external_action_created: Literal[False] = False
