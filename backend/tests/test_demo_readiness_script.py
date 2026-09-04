import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_demo_readiness.py"
SPEC = importlib.util.spec_from_file_location("check_demo_readiness", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_demo_readiness_requires_the_complete_management_chain():
    complete = {
        "source_ready": True,
        "documents": 8,
        "analyzed_documents": 8,
        "contracts": 1,
        "linked_contracts": 1,
        "schedule_rows": 3,
        "budget_rows": 2,
        "cash_flow_rows": 2,
        "contacts": 1,
        "confirmed_contacts": 1,
    }
    assert all(done for _, done, _ in MODULE.readiness_steps(complete))
    complete["linked_contracts"] = 0
    steps = MODULE.readiness_steps(complete)
    assert not dict((title, done) for title, done, _ in steps)["Договор и документ-источник"]


def test_demo_readiness_does_not_accept_an_empty_project():
    assert not any(done for _, done, _ in MODULE.readiness_steps({}))
