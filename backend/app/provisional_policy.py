"""User-authorized provisional settings. No dotenv or external access."""
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class ProvisionalPolicy:
    estimate_rounding: str = "reject_ambiguous"
    negative_stock: str = "forbid"
    journal_delete: str = "block_linked_writeoffs"
    drawing_point: str = "normalized_page_v1"

    def __post_init__(self):
        allowed = {"estimate_rounding": {"reject_ambiguous", "round_half_up"},
                   "negative_stock": {"forbid", "allow_explicit"},
                   "journal_delete": {"block_linked_writeoffs"},
                   "drawing_point": {"normalized_page_v1", "disabled"}}
        for field, values in allowed.items():
            if getattr(self, field) not in values:
                raise ValueError("Unsupported provisional policy: " + field)


def get_policy() -> ProvisionalPolicy:
    return ProvisionalPolicy(
        estimate_rounding=os.getenv("PU_ESTIMATE_ROUNDING_POLICY", "reject_ambiguous"),
        negative_stock=os.getenv("PU_NEGATIVE_STOCK_POLICY", "forbid"),
        journal_delete=os.getenv("PU_WORK_LOG_DELETE_POLICY", "block_linked_writeoffs"),
        drawing_point=os.getenv("PU_DRAWING_POINT_POLICY", "normalized_page_v1"),
    )


def derive_unit_price(qty, total, *, policy=None):
    from decimal import Decimal, ROUND_HALF_UP, localcontext
    policy = policy or get_policy()
    with localcontext() as context:
        context.prec = 50
        if not qty.is_finite() or not total.is_finite() or qty <= 0 or total < 0:
            raise ValueError("Invalid derived price input")
        price = (total / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        represented = (price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if represented != total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) and policy.estimate_rounding == "reject_ambiguous":
            raise ValueError("Неоднозначная цена: укажите цену явно; исходная сумма не изменена")
        return price
