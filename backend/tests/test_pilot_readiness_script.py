import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pilot_readiness.py"
SPEC = importlib.util.spec_from_file_location("check_pilot_readiness", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_pilot_preflight_is_provider_neutral_and_read_only():
    results = MODULE.evaluate(
        {"ready": True},
        {"adapters": [{"provider": "corporate", "available": True, "connected": True}]},
        {"ready": True, "steps": [{"complete": True}, {"complete": True}]},
        {"summary": {"attention": 3}, "external_actions_created": False},
    )

    assert all(ok for _, ok, _ in results)
    assert results[1][2] == "доступно 1, подключено 1"


def test_pilot_preflight_fails_when_launch_is_incomplete():
    results = MODULE.evaluate(
        {"ready": True},
        {"adapters": [{"available": True, "connected": False}]},
        {"ready": False, "steps": [{"complete": True}, {"complete": False}]},
        {"summary": {}, "external_actions_created": False},
    )

    assert not dict((name, ok) for name, ok, _ in results)["Запуск проекта"]


def test_pilot_preflight_accepts_current_launch_readiness_contract():
    results = MODULE.evaluate(
        {"ready": True},
        {"adapters": [{"available": True, "connected": True}]},
        {"completed_steps": 5, "total_steps": 5, "progress": 100},
        {"summary": {}, "external_actions_created": False},
    )

    launch = next(item for item in results if item[0] == "Запуск проекта")
    assert launch == ("Запуск проекта", True, "готово 5 из 5")
