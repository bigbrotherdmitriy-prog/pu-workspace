import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_public_smoke.py"
SPEC = importlib.util.spec_from_file_location("check_public_smoke", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_public_smoke_checks_readiness_and_current_spa(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/new/": (200, '<div id="root"></div><script src="./assets/current.js"></script>'),
        "https://example.test/new/assets/current.js": (200, " ".join(MODULE.REQUIRED_UI_MARKERS)),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    result = MODULE.check_public("https://example.test")

    assert result["ready"] is True
    assert result["markers"] == 4


def test_public_smoke_rejects_stale_frontend(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/new/": (200, '<div id="root"></div><script src="/new/assets/old.js"></script>'),
        "https://example.test/new/assets/old.js": (200, "Запуск проекта"),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    try:
        MODULE.check_public("https://example.test/")
    except RuntimeError as exc:
        assert "incomplete or stale" in str(exc)
    else:
        raise AssertionError("stale frontend must fail production smoke")
