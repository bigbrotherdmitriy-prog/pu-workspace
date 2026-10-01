"""Source-level guards for the image recipe; Docker smoke proves the real build."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_compatibility_recipe_is_an_exact_canonical_mirror():
    canonical = read("backend/Dockerfile")
    mirror = read("Dockerfile.ci").splitlines(keepends=True)
    assert mirror[0].startswith("# Compatibility mirror;")
    assert mirror[1].startswith("# Retained for existing direct callers.")
    assert "".join(mirror[2:]) == canonical


def test_frontend_build_uses_pinned_tools_and_frozen_workspace_lock():
    recipe = read("backend/Dockerfile")
    assert (
        "FROM node:22.20.0-bookworm-slim@sha256:"
        "b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS frontend-build"
    ) in recipe
    assert "npm install --global pnpm@10.17.1" in recipe
    dependencies = "COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./"
    frozen_install = "RUN pnpm install --frozen-lockfile"
    source = "COPY frontend/ ./"
    assert recipe.index(dependencies) < recipe.index(frozen_install) < recipe.index(source)
    assert "pnpm run check && pnpm run test && pnpm run build" in recipe


def test_source_copy_cannot_overwrite_fresh_static_or_publicize_manifest():
    recipe = read("backend/Dockerfile")
    source = "COPY backend/app ./app"
    static = "COPY --from=frontend-build /src/backend/app/react_dist ./app/react_dist"
    manifest = "RUN python -m scripts.frontend_manifest create"
    assert recipe.index(source) < recipe.index(static) < recipe.index(manifest)
    assert "COPY backend/app" not in recipe[recipe.index(static) :]
    assert "--root /app/app/react_dist --output /app/frontend-build-manifest.json" in recipe
    assert "--build-info /app/frontend-build-info.json" in recipe
    assert '--mode "$PU_BUILD_MODE"' in recipe


def test_runtime_preserves_java_mpxj_and_ocr_without_test_fixtures():
    recipe = read("backend/Dockerfile")
    assert (
        "FROM python:3.12.12-slim-bookworm@sha256:"
        "593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS backend-base"
    ) in recipe
    for dependency in (
        "antiword", "default-jre-headless", "poppler-utils", "tesseract-ocr-eng",
        "tesseract-ocr-rus", "tesseract-ocr-osd",
    ):
        assert dependency in recipe
    assert "jpype.startJVM()" in recipe
    assert "UniversalProjectReader" in recipe
    application, targets = recipe.split("FROM application AS testing", 1)
    testing, runtime = targets.split("FROM application AS runtime", 1)
    assert "COPY backend/tests ./tests" in testing
    assert "backend/tests" not in application and "backend/tests" not in runtime
    assert "alembic -c alembic.ini upgrade head" in runtime


def test_all_context_recipes_use_identical_deny_by_default_allowlists():
    ignore = read(".dockerignore")
    assert read("Dockerfile.ci.dockerignore") == ignore
    rules = [line for line in ignore.splitlines() if line and not line.startswith("#")]
    assert rules[0] == "**"
    for rule in (
        "!backend/requirements.txt", "!backend/alembic.ini", "!backend/app/**",
        "!backend/scripts/**", "!backend/migrations/**", "!backend/tests/**", "!frontend/**",
    ):
        assert rule in rules
    last_allow = max(i for i, rule in enumerate(rules) if rule.startswith("!"))
    for rule in (
        "**/.env*", "**/.git", "**/.server-access", "**/.ssh", "**/.aws",
        "**/react_dist", "**/node_modules", "**/__pycache__", "**/.pytest_cache",
        "**/*.pem", "**/*.key", "frontend/dist",
    ):
        assert rules.index(rule) > last_allow
    assert "!backend/app/react_dist" not in rules


def test_ci_and_local_compose_share_canonical_root_context():
    ci = yaml.safe_load(read("docker-compose.ci.yml"))
    local = yaml.safe_load(read("docker-compose.yml"))
    for services in (ci["services"], local["services"]):
        for name in ("backend", "worker", "scheduler"):
            build = services[name]["build"]
            assert build["context"] == "."
            assert build["dockerfile"] == "backend/Dockerfile"
            assert "latest" not in services[name]["image"]
        assert len({services[name]["image"] for name in ("backend", "worker", "scheduler")}) == 1
    assert ci["services"]["backend"]["build"]["target"] == "testing"
    assert ci["services"]["backend"]["build"]["args"]["PU_BUILD_MODE"] == "production"
    assert local["services"]["backend"]["build"]["target"] == "runtime"
    assert local["services"]["backend"]["build"]["args"]["PU_BUILD_MODE"] == "${PU_BUILD_MODE:-development}"
    assert ci["services"]["backend"]["environment"]["GMAIL_AUTO_SYNC_ENABLED"] == "false"


def test_release_mode_fails_closed_but_explicit_local_dev_is_supported():
    recipe = read("backend/Dockerfile")
    assert "ARG PU_BUILD_MODE=production" in recipe
    assert 'test "${#PU_RELEASE_REVISION}" -eq 40' in recipe
    assert "tr -d '0-9a-f'" in recipe
    assert "development:local-dev)" in recipe
    assert "Explicit production SHA or local-dev development mode is required" in recipe
    assert "com.pu-workspace.primary.revision=${PU_RELEASE_REVISION}" in recipe


def test_build_info_uses_actual_tools_and_exact_lockfile_bytes():
    script = read("frontend/scripts/write-build-info.mjs")
    assert "nodeVersion: process.versions.node" in script
    assert 'execFileSync("pnpm", ["--version"]' in script
    assert "buildInfo(readFileSync(values.lockfile)" in script
    assert 'createHash("sha256").update(lockfile).digest("hex")' in script
    assert 'flag: "wx"' in script
    assert "image_digest" not in script and "timestamp" not in script


def test_docker_smoke_checks_image_id_and_every_loopback_asset():
    workflow = yaml.safe_load(read(".github/workflows/docker-smoke.yml"))
    steps = workflow["jobs"]["docker-smoke"]["steps"]
    verifier = next(step for step in steps if step.get("name") == "Verify image-derived frontend manifest and every HTTP asset")
    command = verifier["run"]
    assert "scripts/verify_image_static.py" in command
    assert '--expected-image-id "$image_id" --revision "$revision"' in command
    assert '--loopback-url "http://127.0.0.1:$port"' in command
    assert "--receipt ci-reports/frontend-static-verification.json" in command
    assert "--compose-project" not in command
