from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend" / "src" / "App.tsx"
MODULE = ROOT / "frontend" / "src" / "modules" / "governance" / "GovernanceModule.tsx"


def test_governance_registry_is_extracted_from_app_shell():
    app = APP.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")

    assert 'from "./modules/governance/GovernanceModule"' in app
    assert "<GovernanceModule" in app
    assert 'className="governance-grid"' not in app
    assert 'className="governance-grid"' in module


def test_governance_actions_keep_human_confirmation():
    module = MODULE.read_text(encoding="utf-8")

    assert 'onUpdateRisk(risk, "confirmed")' in module
    assert 'onUpdateRisk(risk, "resolved")' in module
    assert 'onUpdateDecision(decision, "decided")' in module
    assert 'onUpdateDecision(decision, "dismissed")' in module
    assert "source_name" in module
    assert "confidence" in module
