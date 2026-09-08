import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v7_ci_capacity import capacity


def test_failed_counts_reason_and_repository_node():
    m = capacity()
    node = "backend/tests/test_v7_draft_send_precondition.py::test_request_body_contract_is_exposed"
    p = m.evaluate_offline(1, f"FAILED {node}[private@example.invalid] - token=secret\n1 failed, 2416 passed, 55 skipped in 380.59s\n")
    assert p["failed"] == 1 and p["errors"] == 0
    assert p["failure_code"] == "TEST_FAILURE"
    assert p["failed_test_ids"] == [node]
    assert "private" not in json.dumps(p) and "secret" not in json.dumps(p)


@pytest.mark.parametrize("line", [
    "FAILED backend/tests/private_customer.py::test_secret - value",
    "FAILED /home/private/test_secret.py::test_customer",
    "FAILED backend/tests/../../secret.py::test_customer",
    "FAILED backend/tests/test_v7_draft_send_precondition.py::test_unknown_secret",
    "FAILED https://secret.invalid/token",
])
def test_unregistered_identifiers_are_never_published(line):
    p = capacity().evaluate_offline(1, line + "\n1 failed in 1s\n")
    assert p["failed_test_ids"] == []
    assert "secret" not in json.dumps(p) and "private" not in json.dumps(p)


@pytest.mark.parametrize("code,summary,reason", [(0,"","SUMMARY_MISSING"),
    (2,"1 error in 1s","COLLECTION_OR_INTERRUPTED"),
    (3,"1 passed in 1s","PYTEST_INTERNAL"), (4,"","PYTEST_USAGE"),
    (5,"","NO_TESTS_COLLECTED"), (-9,"","UNEXPECTED_EXIT"),
    (0,"1 passed, 1 xfailed in 1s","ACCEPTANCE_COUNTS_REJECTED")])
def test_safe_reason_codes(code,summary,reason):
    assert capacity().evaluate_offline(code,summary)["failure_code"] == reason


def test_timeout_never_parses_partial_output(monkeypatch,tmp_path):
    m=capacity(); monkeypatch.setattr(m,"ROOT",tmp_path)
    def run(*args,**kwargs):
        raise m.subprocess.TimeoutExpired("private",900,output="1 failed, 2 passed in 1s",stderr="secret")
    monkeypatch.setattr(m.subprocess,"run",run)
    assert m.run_offline()==1
    p=json.loads((tmp_path/"v7-backend-offline-artifacts/protocol.json").read_text())
    assert p["failure_code"]=="TIMEOUT" and p["failed_test_ids"]==[] and p["passed"]==0


def test_collection_file_is_registered_but_dynamic_parameter_is_not():
    m=capacity(); file="backend/tests/test_v7_draft_send_precondition.py"
    p=m.evaluate_offline(2,f"ERROR {file} - private DSN\n1 error in 1s\n")
    assert p["failed_test_ids"]==[file] and p["errors"]==1


def test_allowlist_reads_ast_without_import_and_strips_class_parameters(monkeypatch,tmp_path):
    m=capacity(); monkeypatch.setattr(m,"ROOT",tmp_path)
    directory=tmp_path/"backend/tests"; directory.mkdir(parents=True)
    (directory/"test_synthetic.py").write_text(
        "raise RuntimeError('module must never be imported')\n"
        "class TestSynthetic:\n    def test_example(self): pass\n",encoding="utf8")
    node="backend/tests/test_synthetic.py::TestSynthetic::test_example"
    p=m.evaluate_offline(1,f"FAILED {node}[private-person-token] - private traceback\n1 failed in 1s")
    assert p["failed_test_ids"]==[node]
    assert "private" not in json.dumps(p)
