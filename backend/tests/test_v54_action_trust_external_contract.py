"""Future external UNKNOWN CONTRACT ONLY. No external executor/attempt schema.

Fake provider effect counter lives independently of the simulated worker state.
This is not process-crash testing or proof of provider reconciliation support.
The only production function exercised is the fail-closed business-state guard.
"""
import pytest

from app.action_trust.guards import TrustConflict
from app.action_trust.state import require_new_effect


class FakeProvider:
    def __init__(self):
        self.effects = 0

    def mutate_then_lose_response(self):
        self.effects += 1
        raise TimeoutError("synthetic_timeout")

    def search(self, *, authoritative=False):
        return "APPLIED" if authoritative and self.effects else "UNKNOWN"


def test_unknown_survives_lease_expiry_empty_search_and_late_receipt():
    provider = FakeProvider()
    state = "READY"
    require_new_effect(state)
    state = "EXECUTING"
    with pytest.raises(TimeoutError):
        provider.mutate_then_lose_response()
    state = "UNKNOWN"
    for simulated_lease_generation in [2, 3, 4]:
        assert provider.search() == "UNKNOWN"
        with pytest.raises(TrustConflict, match="outcome_not_replayable"):
            require_new_effect(state)
            provider.mutate_then_lose_response()
        assert provider.effects == 1
    assert provider.search(authoritative=True) == "APPLIED"
    state = "SUCCEEDED"
    with pytest.raises(TrustConflict):
        require_new_effect(state)
    assert provider.effects == 1


@pytest.mark.parametrize("state", ["UNKNOWN", "EXECUTING", "FAILED_NOT_APPLIED", "SUCCEEDED"])
def test_transport_retry_is_not_new_execution_permission(state):
    # Even authoritative NOT_APPLIED needs a new gate, not an attempt receipt
    # masquerading as the foundation's unique ActionReceipt business result.
    with pytest.raises(TrustConflict):
        require_new_effect(state)
