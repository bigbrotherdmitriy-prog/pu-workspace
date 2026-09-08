"""Guard runner service arguments (not shell commands).

GitHub runner passes service options to docker without a POSIX shell.
Single quotes do not protect spaces there; require double-quoted health cmds.
This static contract does not substitute for actual container startup.
"""
from pathlib import Path
import re

import pytest


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def validate_health_options(text):
    for line in text.splitlines():
        if "--health-cmd" in line:
            assert re.search(r'--health-cmd\s+"[^"\r\n]+"(?:\s|$)', line), (
                "Docker service health command must use runner-compatible double quotes"
            )


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_service_health_commands_are_runner_compatible(path):
    validate_health_options(path.read_text(encoding="utf-8"))


def test_rejects_single_quoted_multi_argument_command():
    with pytest.raises(AssertionError):
        validate_health_options("--health-cmd 'pg_isready -U puw_ci -d postgres'")


def test_accepts_double_quoted_multi_argument_command():
    validate_health_options('--health-cmd "pg_isready -U puw_ci -d postgres"')
