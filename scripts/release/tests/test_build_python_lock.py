from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "build_python_lock.py"
SPEC = importlib.util.spec_from_file_location("build_python_lock", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def report(*, platform: str = "linux", machine: str = "x86_64", digest: str = "a" * 64):
    return {
        "version": "1",
        "pip_version": "26.2.1",
        "environment": {
            "implementation_name": "cpython",
            "python_version": "3.12",
            "sys_platform": platform,
            "platform_machine": machine,
        },
        "install": [{
            "requested": True,
            "is_yanked": False,
            "metadata": {"name": "Example_Pkg", "version": "1.2.3"},
            "download_info": {
                "url": "https://files.pythonhosted.org/packages/example_pkg-1.2.3-py3-none-any.whl",
                "archive_info": {"hashes": {"sha256": digest}},
            },
        }],
    }


def test_linux_report_becomes_exact_hash_lock():
    rows = MODULE.parse_report(report(), b"Example-Pkg==1.2.3\n")
    rendered = MODULE.render_lock(rows, "b" * 64, "26.2.1")

    assert rows[0]["name"] == "example-pkg"
    assert "--only-binary=:all:" in rendered
    assert "--require-hashes" in rendered
    assert "example-pkg==1.2.3" in rendered
    assert "--hash=sha256:" + "a" * 64 in rendered


def test_cross_platform_report_is_rejected():
    with pytest.raises(MODULE.LockError, match="CPython 3.12 on Linux"):
        MODULE.parse_report(report(platform="win32", machine="AMD64"), b"Example-Pkg==1.2.3\n")


@pytest.mark.parametrize("mutation", ["missing-hash", "foreign-host", "yanked", "direct-mismatch"])
def test_unsafe_resolution_is_rejected(mutation):
    value = report()
    requirements = b"Example-Pkg==1.2.3\n"
    if mutation == "missing-hash":
        value["install"][0]["download_info"]["archive_info"]["hashes"] = {}
    elif mutation == "foreign-host":
        value["install"][0]["download_info"]["url"] = "https://example.invalid/pkg.whl"
    elif mutation == "yanked":
        value["install"][0]["is_yanked"] = True
    else:
        requirements = b"Other==1.2.3\n"

    with pytest.raises(MODULE.LockError):
        MODULE.parse_report(value, requirements)


def test_direct_requirements_must_be_exact_pins():
    with pytest.raises(MODULE.LockError, match="not an exact pin"):
        MODULE.direct_pins("example>=1\n")
