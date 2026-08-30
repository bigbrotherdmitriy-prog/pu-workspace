from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_audit_module_is_extracted_and_keeps_search():
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend/src/modules/audit/AuditModule.tsx").read_text(encoding="utf-8")
    assert "<AuditModule" in app
    assert "Журнал действий" in module
    assert "toLocaleLowerCase" in module
    assert "item.details" in module
    assert "onReload" in module
