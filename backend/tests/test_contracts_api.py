from app.api.organizations_contracts import ContractCreate, router


def test_contract_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/organizations" in paths
    assert "/projects/{project_id}/contracts" in paths


def test_contract_payload_defaults_to_active():
    payload = ContractCreate(number="DCI-01", title="Основной договор")
    assert payload.status == "active"
