import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prune_primary_artifacts.py"
DEPLOY = ROOT / "scripts" / "deploy-primary-first-host.sh"


def load():
    spec = importlib.util.spec_from_file_location("prune_primary_artifacts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(n):
    return f"{n:040x}"


def image(n):
    return "sha256:" + f"{n:064x}"


def test_plan_images_keeps_pinned_and_two_newest_others():
    prune = load()
    tags = [(f"app-backend:{sha(n)}", image(n), f"2026-10-{n:02d}T00:00:00Z") for n in range(1, 8)]
    # candidate=7, previous=6, an old release still pins 2.
    removed = prune.plan_images(tags, {image(7), image(6), image(2)})
    assert removed == [f"app-backend:{sha(n)}" for n in (1, 3)]


def test_plan_images_counts_an_image_once_when_it_has_several_tags():
    prune = load()
    tags = [("app-backend:" + sha(1), image(1), "2026-10-01"), ("app-backend:" + sha(2), image(1), "2026-10-01"),
            ("app-backend:" + sha(3), image(3), "2026-10-03"), ("app-backend:" + sha(4), image(4), "2026-10-04")]
    assert prune.plan_images(tags, {image(4)}) == []


def test_plan_releases_never_removes_current_or_previous(tmp_path):
    prune = load()
    entries = [(tmp_path / sha(n), float(n)) for n in range(1, 9)]
    current, previous = tmp_path / sha(1), tmp_path / sha(2)
    removed = prune.plan_releases(entries, {current, previous})
    # Newest three others (8, 7, 6) stay; oldest current/previous are protected.
    assert removed == [tmp_path / sha(n) for n in (5, 4, 3)]


def test_repository_is_taken_only_from_a_full_sha_tag():
    prune = load()
    assert prune.repository_of("app-backend:" + sha(1)) == "app-backend"
    assert prune.repository_of("registry:5000/puw-primary:" + sha(1)) == "registry:5000/puw-primary"
    assert prune.repository_of("app-backend:latest") is None
    assert prune.repository_of(image(1)) is None


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_DOCKER_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
state["calls"].append(args)
def save(): json.dump(state, open(state_path, "w"))
if state.get("fail_all"):
    save(); sys.exit(1)
if args[:2] == ["image", "ls"]:
    print("\n".join(ref for ref in state["tags"] if ref.startswith(args[2] + ":")))
elif args[:2] == ["image", "inspect"]:
    ref = args[2]
    if ref not in state["tags"]:
        save(); sys.exit(1)
    item = state["tags"][ref]
    print(item["id"] if args[4] == "{{.Id}}" else f'{item["id"]} {item["created"]}')
elif args[0] == "rmi":
    if state["tags"][args[1]]["id"] in state.get("in_use", []):
        save(); sys.exit(1)
    del state["tags"][args[1]]
elif args[:2] == ["image", "prune"]:
    state["pruned"] = True
save()
'''


def host(tmp_path, *, fail_all=False, in_use=()):
    root = tmp_path / "primary"
    for n in range(1, 8):
        release = root / "releases" / sha(n)
        release.mkdir(parents=True)
        os.utime(release, (n * 1000, n * 1000))
    (root / "releases" / "not-a-sha").mkdir()
    runtime = root / "runtime" / sha(5)
    runtime.mkdir(parents=True)
    (runtime / ".env.primary").write_text(f"PRIMARY_IMAGE={image(2)}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "docker.json"
    state.write_text(json.dumps({
        "tags": {f"app-backend:{sha(n)}": {"id": image(n), "created": f"2026-10-{n:02d}T00:00:00Z"} for n in range(1, 8)}
        | {"other-app:" + sha(9): {"id": image(9), "created": "2026-09-01T00:00:00Z"}},
        "calls": [], "fail_all": fail_all, "in_use": list(in_use),
    }), encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER_STATE": str(state)}
    return root, env, state


def run(root, env, *, previous=6):
    return subprocess.run([
        sys.executable, str(SCRIPT), "--root", str(root),
        "--release", str(root / "releases" / sha(7)),
        "--previous-release", str(root / "releases" / sha(previous)),
        "--candidate-ref", f"app-backend:{sha(7)}", "--candidate-image", image(7),
        "--previous-image", image(previous),
    ], env=env, capture_output=True, text=True, timeout=60)


def test_prune_keeps_rollback_sets_and_removes_the_rest(tmp_path):
    root, env, state = host(tmp_path)
    result = run(root, env)
    assert result.returncode == 0, result.stderr
    # Releases: candidate 7 + previous 6 + newest others 5, 4, 3; non-SHA dirs untouched.
    assert sorted(p.name for p in (root / "releases").iterdir()) == sorted([sha(n) for n in (3, 4, 5, 6, 7)] + ["not-a-sha"])
    data = json.loads(state.read_text())
    # Images: 7 and 6, image 2 pinned by retained release 5, newest others 5 and 4.
    assert sorted(data["tags"]) == sorted([f"app-backend:{sha(n)}" for n in (2, 4, 5, 6, 7)] + ["other-app:" + sha(9)])
    assert data["pruned"] is True
    assert all("-f" not in call for call in data["calls"] if call[0] == "rmi")


def test_prune_errors_never_fail_and_in_use_images_stay(tmp_path):
    root, env, state = host(tmp_path, in_use=[image(1)])
    assert run(root, env).returncode == 0
    assert f"app-backend:{sha(1)}" in json.loads(state.read_text())["tags"]

    root2, env2, state2 = host(tmp_path / "broken", fail_all=True)
    result = run(root2, env2)
    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert len(json.loads(state2.read_text())["tags"]) == 8


def test_deploy_prunes_only_after_a_healthy_cutover_and_never_fails_on_it():
    source = DEPLOY.read_text(encoding="utf-8")
    call = source.index("scripts/prune_primary_artifacts.py")
    # After smoke checks and after the rollback trap is disarmed ...
    assert call > source.index('static_smoke "$RELEASE_DIR" "$CANDIDATE_IMAGE" candidate\n\n')
    assert call > source.rindex("trap - INT TERM HUP EXIT")
    # ... and a cleanup failure is only logged.
    statement = source[call:source.index("\n\n", call)]
    assert "|| echo" in statement
    # The repository comes from the reference given to the deploy, before it is resolved to an ID.
    assert source.index("CANDIDATE_REF=$CANDIDATE_IMAGE") < source.index(
        "CANDIDATE_IMAGE=$(docker image inspect \"$CANDIDATE_IMAGE\" --format '{{.Id}}')")
