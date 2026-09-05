import importlib.util
from pathlib import Path
import re
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_ci_browser.py"
WORKSPACE_THEME = SCRIPT.parents[1] / "frontend" / "src" / "interface-v6-workspace.css"
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
    assert 'page.locator("aside nav").get_by_role(' in source
    for label in ("Документы", "Задачи", "Интеграции"):
        assert f'"button", name="{label}", exact=True' in source
        assert f'page.get_by_text("{label}", exact=True).first.click()' not in source


def test_primary_navigation_stays_above_fixed_module_workspaces():
    source = WORKSPACE_THEME.read_text(encoding="utf-8")
    ambient_rule = re.search(r"\.pu-ambient\s*\{(?P<body>[^}]*)\}", source, re.DOTALL)
    assert ambient_rule is not None
    assert re.search(r"\bposition\s*:\s*fixed\s*;", ambient_rule.group("body"))
    assert re.search(r"\binset\s*:\s*0\s*;", ambient_rule.group("body"))
    sidebar_rule = re.search(r"\.shell\s*>\s*aside\s*\{(?P<body>[^}]*)\}", source, re.DOTALL)
    assert sidebar_rule is not None
    assert re.search(r"\bz-index\s*:\s*20\s*;", sidebar_rule.group("body"))
    assert re.search(r"\boverflow-x\s*:\s*clip\s*;", sidebar_rule.group("body"))
