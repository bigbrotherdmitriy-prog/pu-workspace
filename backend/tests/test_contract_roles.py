from app.core.contract_roles import (
    allowed_parent_kinds,
    cash_flow_direction,
    is_financial_contract,
)


def test_prime_contract_is_context_not_financial_source():
    assert not is_financial_contract("prime_reference")
    assert cash_flow_direction("prime_reference") is None


def test_our_subcontract_is_revenue_and_links_to_prime_contract():
    assert is_financial_contract("revenue_subcontract")
    assert cash_flow_direction("revenue_subcontract") == "inflow"
    assert allowed_parent_kinds("revenue_subcontract") == {"prime_reference"}


def test_downstream_subcontract_and_supply_are_cost_contracts():
    assert cash_flow_direction("downstream_subcontract") == "outflow"
    assert cash_flow_direction("supply") == "outflow"
    assert allowed_parent_kinds("downstream_subcontract") == {"customer", "revenue_subcontract", "downstream_subcontract"}


def test_subcontract_chain_can_continue_to_any_depth():
    assert "downstream_subcontract" in allowed_parent_kinds("downstream_subcontract")
    assert "downstream_subcontract" in allowed_parent_kinds("supply")
