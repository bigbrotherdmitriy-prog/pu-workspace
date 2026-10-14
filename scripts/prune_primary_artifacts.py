"""Remove old primary images and release directories after a healthy deploy.

Every deploy builds a new ``<repo>:<full-sha>`` image (0.3-0.5 GB) and leaves a
release directory behind; nothing removed them, and on 14.10.2026 the host disk
reached 80 %. This runs only after the candidate passed its smoke checks.

Kept for rollback (docs/PRIMARY_FIRST_HOST_DEPLOYMENT.md asks for the current
and two previous accepted sets):
- images: the candidate, the image the previous release ran, every image pinned
  in a retained release's runtime env, and the two newest other ``<repo>:<sha>``
  tags; dangling layers are pruned. Every reference to keep (ID or tag) is
  resolved to an image ID first; if any cannot be resolved, no tag is removed;
- releases: the candidate, the previous release and the three newest others.

The deploy is already live, so nothing here may fail it: every error is logged
and the script exits 0. ``docker rmi`` runs without ``--force`` so an image a
container still uses is never removed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}")
KEEP_IMAGES = 2
KEEP_RELEASES = 3


def log(message: str) -> None:
    print(f"PRIMARY_PRUNE {message}", flush=True)


def plan_images(tags: list[tuple[str, str, str]], keep_ids: set[str], keep_newest: int = KEEP_IMAGES) -> list[str]:
    """Return tag references to remove.

    ``tags`` holds (reference, image id, created) for ``<repo>:<sha>`` tags.
    ``keep_ids`` are always kept; of the remaining images the ``keep_newest``
    newest are kept too.
    """
    kept = set(keep_ids)
    newest_first = sorted({(created, image_id) for _, image_id, created in tags}, reverse=True)
    extra = 0
    for _, image_id in newest_first:
        if image_id in kept:
            continue
        if extra >= keep_newest:
            break
        kept.add(image_id)
        extra += 1
    return [reference for reference, image_id, _ in tags if image_id not in kept]


def plan_releases(entries: list[tuple[Path, float]], keep: set[Path], keep_newest: int = KEEP_RELEASES) -> list[Path]:
    """Return release directories to remove; ``entries`` holds (path, mtime)."""
    others = sorted((item for item in entries if item[0] not in keep), key=lambda item: item[1], reverse=True)
    return [path for path, _ in others[keep_newest:]]


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=120).stdout


def image_id(reference: str) -> str | None:
    try:
        return docker("image", "inspect", reference, "--format", "{{.Id}}").strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def repository_of(reference: str) -> str | None:
    """``repo:<40-hex>`` -> ``repo``; anything else (an ID, a moving tag) -> None."""
    repository, separator, tag = reference.rpartition(":")
    if not separator or "/" in tag or not SHA.fullmatch(tag) or not repository:
        return None
    return repository


def sha_tags(repository: str) -> list[tuple[str, str, str]]:
    listing = docker("image", "ls", repository, "--no-trunc", "--format", "{{.Repository}}:{{.Tag}}")
    tags = []
    for reference in listing.split():
        if repository_of(reference) != repository:
            continue
        details = docker("image", "inspect", reference, "--format", "{{.Id}} {{.Created}}").split()
        if len(details) == 2:
            tags.append((reference, details[0], details[1]))
    return tags


def pinned_reference(runtime_dir: Path, release: Path) -> str | None:
    """The raw ``PRIMARY_IMAGE`` value (an ID or a tag) of a retained release."""
    env = runtime_dir / release.name / ".env.primary"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PRIMARY_IMAGE="):
                return line.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def resolve_keep(references: set[str]) -> tuple[set[str], set[str]] | None:
    """Resolve every reference to keep into (image IDs, tags).

    Any reference that cannot be resolved makes the caller skip tag removal:
    without its ID we cannot prove a rollback image is not among the deletions.
    """
    ids, tags = set(), set()
    for reference in references:
        resolved = image_id(reference)
        if resolved is None:
            log(f"keep_unresolved={reference}")
            return None
        ids.add(resolved)
        if not reference.startswith("sha256:"):
            tags.add(reference)
    return ids, tags


def release_dirs(releases: Path) -> list[tuple[Path, float]]:
    entries = []
    for path in releases.iterdir():
        if SHA.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
            entries.append((path, path.stat().st_mtime))
    return entries


def prune(args: argparse.Namespace) -> None:
    root = Path(args.root)
    releases = root / "releases"
    current = Path(args.release)
    previous = Path(args.previous_release) if args.previous_release else None
    keep_releases = {current} | ({previous} if previous else set())

    removed_releases: list[Path] = []
    try:
        entries = release_dirs(releases)
        for path in plan_releases(entries, keep_releases):
            # Same guard as the deploy: only real SHA directories under ROOT/releases.
            if path.parent != releases or path.is_symlink():
                continue
            try:
                shutil.rmtree(path)
                removed_releases.append(path)
                log(f"release_removed={path.name}")
            except OSError as error:
                log(f"release_kept={path.name} reason={error.__class__.__name__}")
    except OSError as error:
        log(f"releases_skipped reason={error.__class__.__name__}")
        entries = []

    # Pins are read for the releases that are still on disk after the removal above.
    retained = [path for path, _ in entries if path not in removed_releases] + [current]
    references = {args.candidate_ref, args.candidate_image, args.previous_image}
    references |= {pinned_reference(root / "runtime", release) for release in retained}
    references = {reference for reference in references if reference}

    repository = repository_of(args.candidate_ref)
    keep = resolve_keep(references)
    if repository is None:
        log("image_tags_skipped reason=candidate_reference_is_not_repo_sha_tag")
    elif keep is None:
        log("image_tags_skipped reason=keep_reference_unresolved")
    else:
        keep_ids, keep_tags = keep
        try:
            for reference in plan_images(sha_tags(repository), keep_ids):
                if reference in keep_tags:
                    continue
                try:
                    docker("rmi", reference)
                    log(f"image_removed={reference}")
                except (subprocess.SubprocessError, OSError):
                    log(f"image_kept={reference} reason=rmi_failed")
        except (subprocess.SubprocessError, OSError) as error:
            log(f"image_tags_skipped reason={error.__class__.__name__}")
    try:
        docker("image", "prune", "-f")
        log("dangling_images_pruned")
    except (subprocess.SubprocessError, OSError) as error:
        log(f"dangling_prune_skipped reason={error.__class__.__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--previous-release", default="")
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--previous-image", default="")
    args = parser.parse_args(argv)
    try:
        prune(args)
    except Exception as error:  # noqa: BLE001 - a live deploy must never fail on cleanup
        log(f"failed reason={error.__class__.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
