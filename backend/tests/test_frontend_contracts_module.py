from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contract_create_flow_is_owned_by_contracts_module():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module_source = (ROOT / "frontend" / "src" / "modules" / "contracts" / "ContractsModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/contracts/ContractsModule"' in app_source
    assert "<ContractsModule" in app_source
    assert "Добавить договор" in module_source
    assert "contract-form" in module_source
