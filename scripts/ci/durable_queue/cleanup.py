"""Fallback for cancelled/failed runner; accepts only its saved unique project."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
state = root / "queue-runtime-state.json"
if state.exists():
    data = json.loads(state.read_text())
    project = data["project"]
    assert project == f"puw-queue-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    assert re.fullmatch(r"puw-queue-\d+-\d+", project)
    env_file = Path(data["env_file"]).resolve()
    assert env_file.name == "ci.env"
    assert env_file.parent.parent == Path(tempfile.gettempdir()).resolve()
    assert env_file.parent.name.startswith("puw-queue-")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "WINDIR"}}
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    dc = ["docker", "compose", "--project-name", project, "--file", str(root / "docker-compose.queue-ci.yml"), "--env-file", str(env_file)]
    def run(args):
        r = subprocess.run(args, capture_output=True, timeout=100, env=env)
        assert r.returncode == 0, "cleanup command failed (raw output withheld)"
        return r.stdout
    # Save safe diagnostic status before deletion, even on cancellation.
    out = root / "queue-artifacts"
    out.mkdir(exist_ok=True)
    (out / "fallback-cleanup.json").write_text(json.dumps({"project": project, "cleanup": "started"}))
    run(dc + ["down", "--volumes", "--remove-orphans", "--timeout", "10"])
    for args in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"]):
        assert not run(["docker", *args, "--filter", f"label=com.docker.compose.project={project}"]).strip()
    (out / "fallback-cleanup.json").write_text(json.dumps({"project": project, "cleanup": "PASS"}))
    env_file.unlink()
    env_file.parent.rmdir()
    state.unlink()
