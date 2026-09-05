import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_ci_browser.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("check_ci_browser", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_browser_smoke_is_confined_to_nonproduction_loopback():
    assert MODULE.browser_base({"PU_TEST_PORT": "3010"}) == "http://127.0.0.1:3010"
    for unsafe_port in ("80", "3000", "70000"):
        with pytest.raises(ValueError):
            MODULE.browser_base({"PU_TEST_PORT": unsafe_port})


def test_browser_smoke_uses_synthetic_input_and_blocks_external_integrations():
    source = SCRIPT.read_text(encoding="utf-8")
    assert MODULE.SYNTHETIC_DOCUMENT.endswith(".txt")
    assert "Все сведения синтетические" in MODULE.SYNTHETIC_CONTENT
    for marker in ("/google/auth", "/google/files", "/gmail/sync", "/send-gmail"):
        assert marker in MODULE.FORBIDDEN_EXTERNAL_PATHS
    assert "external_requests" in source
    assert 'route.abort("blockedbyclient")' in source
    assert "local-upload/analyze" in source
    assert "wait_for_jobs(page, base, job_ids)" in source
    assert "/admin/jobs?limit=100" in source
    assert 'page.get_by_text("Документы"' in source
    assert 'page.get_by_text("Задачи"' in source
    assert 'page.get_by_text("Интеграции"' in source
