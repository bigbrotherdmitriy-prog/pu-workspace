"""Create and verify a complete, immutable frontend build inventory (stdlib only)."""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.+-]+)?\Z")
_BUILD_INFO_KEYS = {"node_version", "pnpm_version", "lockfile_sha256"}
_MANIFEST_KEYS = {"schema_version", "revision", "build_mode", "build_info", "files"}


def _relative_path(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).as_posix() != value):
        raise ValueError(f"Invalid frontend file path: {value!r}")
    return value


def _is_link(path):
    # Junctions must not let a Windows verification traverse outside the root.
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _root_path(root):
    path = Path(root)
    if _is_link(path):
        raise ValueError(f"Frontend root is a symlink or junction: {path}")
    if not path.is_dir():
        raise ValueError(f"Frontend root is not a directory: {path}")
    return path.resolve(strict=True)


def _file_record(path, relative):
    if _is_link(path):
        raise ValueError(f"Frontend symlink or junction is forbidden: {relative}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Frontend entry is not a regular file: {relative}")
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError(f"Frontend entry is not a regular file: {relative}")
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"path": _relative_path(relative), "size": size, "sha256": digest.hexdigest()}


class _IndexAssets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        relevant = {"src", "srcset", "href", "poster", "data", "rel"}
        if any(sum(name == key for name, _ in attrs) > 1 for key in relevant):
            raise ValueError("Duplicate asset attribute in frontend index.html")
        if tag == "base":
            # The deployed app has /new/ as its public base; relative references
            # can otherwise acquire a meaning unrelated to the inventory root.
            if attributes.get("href", "") not in {"/", "/new/", "./"}:
                raise ValueError("Unsupported base href in frontend index.html")
        if tag == "link":
            resource_rel = {"stylesheet", "modulepreload", "preload", "prefetch", "icon",
                            "apple-touch-icon", "mask-icon", "manifest"}
            if set(attributes.get("rel", "").lower().split()) & resource_rel:
                self.references.append(attributes.get("href"))
        if tag in {"script", "img", "audio", "video", "source", "track", "embed", "iframe", "input"}:
            if "src" in attributes:
                self.references.append(attributes["src"])
        if tag == "video" and "poster" in attributes:
            self.references.append(attributes["poster"])
        if tag == "object" and "data" in attributes:
            self.references.append(attributes["data"])
        if tag in {"img", "source"} and "srcset" in attributes:
            self.references.extend(_srcset_references(attributes["srcset"]))


def _srcset_references(value):
    # A data URL contains a comma. HTML srcset separates its URL from optional
    # descriptors with whitespace, rather than splitting every comma blindly.
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Empty srcset asset reference in frontend index.html")
    cursor = 0
    while cursor < len(value):
        while cursor < len(value) and (value[cursor].isspace() or value[cursor] == ","):
            cursor += 1
        start = cursor
        while cursor < len(value) and not value[cursor].isspace():
            cursor += 1
        url = value[start:cursor]
        if not url:
            break
        yield url.rstrip(",")
        if url.endswith(","):
            continue
        while cursor < len(value) and value[cursor] != ",":
            cursor += 1


def _asset_path(reference):
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("Empty asset reference in frontend index.html")
    parsed = urlsplit(reference.strip())
    if parsed.scheme in {"http", "https", "data", "blob"}:
        return None  # These URLs do not name files in the build directory.
    if parsed.scheme:
        raise ValueError(f"Unsupported asset URL in frontend index.html: {reference!r}")
    if parsed.netloc:
        return None  # A protocol-relative external URL.
    path = unquote(parsed.path, errors="strict")
    if path.startswith("//"):
        raise ValueError(f"Invalid asset URL in frontend index.html: {reference!r}")
    if path.startswith("/new/"):
        path = path[len("/new/"):]
    elif path.startswith("/"):
        path = path[1:]
    while path.startswith("./"):
        path = path[2:]
    return _relative_path(path)


def _validate_index(root, paths):
    if "index.html" not in paths:
        raise ValueError("Frontend build is missing index.html")
    parser = _IndexAssets()
    parser.feed((root / "index.html").read_text(encoding="utf-8"))
    parser.close()
    for reference in parser.references:
        path = _asset_path(reference)
        if path is not None and path not in paths:
            raise ValueError(f"Missing index.html asset: {path}")


def scan_files(root):
    """Return sorted records for all files, rejecting unsafe or incomplete builds."""
    root = _root_path(root)
    records = []
    try:
        def walk_error(error):
            raise error

        for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
            parent = Path(directory)
            for name in directories:
                candidate = parent / name
                if _is_link(candidate):
                    raise ValueError(f"Frontend symlink or junction is forbidden: {candidate.relative_to(root)}")
                if not stat.S_ISDIR(candidate.lstat().st_mode):
                    raise ValueError(f"Frontend entry is not a directory: {candidate.relative_to(root)}")
            for name in filenames:
                path = parent / name
                records.append(_file_record(path, path.relative_to(root).as_posix()))
        if not records:
            raise ValueError("Frontend build directory is empty")
        records.sort(key=lambda record: record["path"])
        _validate_index(root, {record["path"] for record in records})
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read frontend build: {exc}") from exc
    return records


def validate_files(root, files):
    """Verify an entire supplied inventory, including its index asset references."""
    if not isinstance(files, list) or not files:
        raise ValueError("Frontend file inventory must be a nonempty list")
    expected = {}
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise ValueError("Invalid frontend file record")
        path = _relative_path(record["path"])
        if path in expected:
            raise ValueError(f"Duplicate frontend file path: {path}")
        if type(record["size"]) is not int or record["size"] < 0:
            raise ValueError(f"Invalid frontend file size: {path}")
        if not isinstance(record["sha256"], str) or not _SHA256.fullmatch(record["sha256"]):
            raise ValueError(f"Invalid frontend file sha256: {path}")
        expected[path] = record
    if list(expected) != sorted(expected):
        raise ValueError("Frontend file inventory must be sorted by path")
    actual = {record["path"]: record for record in scan_files(root)}
    missing, extra = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
    if missing or extra:
        raise ValueError(f"Frontend inventory differs: missing={missing}, extra={extra}")
    for path, record in expected.items():
        if record != actual[path]:
            raise ValueError(f"Frontend file size or sha256 mismatch: {path}")
    return files


def _validate_identity(revision, mode, build_info):
    if mode not in {"production", "development"}:
        raise ValueError("Frontend build mode must be production or development")
    if not isinstance(revision, str) or (
            mode == "production" and not _REVISION.fullmatch(revision)
            or mode == "development" and revision != "local-dev"):
        raise ValueError("Production revision must be a lowercase 40-character SHA; development must use local-dev")
    if not isinstance(build_info, dict) or set(build_info) != _BUILD_INFO_KEYS:
        raise ValueError("Build info requires node_version, pnpm_version and lockfile_sha256")
    for key in ("node_version", "pnpm_version"):
        if not isinstance(build_info[key], str) or not _VERSION.fullmatch(build_info[key]):
            raise ValueError(f"Invalid frontend build info {key}")
    if not isinstance(build_info["lockfile_sha256"], str) or not _SHA256.fullmatch(build_info["lockfile_sha256"]):
        raise ValueError("Invalid frontend build info lockfile_sha256")


def create_manifest(root, revision, build_info, mode="production"):
    """Build schema 1 metadata; the caller must store it outside the public root."""
    _validate_identity(revision, mode, build_info)
    return {"schema_version": SCHEMA_VERSION, "revision": revision, "build_mode": mode,
            "build_info": dict(build_info), "files": scan_files(root)}


def validate_manifest(manifest, root, expected_revision):
    """Return a verified manifest, or raise ValueError without partial acceptance."""
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("Invalid frontend manifest fields")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported frontend manifest schema_version")
    _validate_identity(manifest["revision"], manifest["build_mode"], manifest["build_info"])
    if manifest["revision"] != expected_revision:
        raise ValueError(f"Frontend revision mismatch: expected {expected_revision!r}, got {manifest['revision']!r}")
    validate_files(root, manifest["files"])
    return manifest


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Write a verified frontend build manifest")
    create.add_argument("--root", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--revision", required=True)
    create.add_argument("--build-info", required=True, help="JSON object or path to a JSON file")
    create.add_argument("--mode", choices=("production", "development"), default="production")
    args = parser.parse_args(argv)
    try:
        root, output = _root_path(args.root), Path(args.output)
        if output.resolve().is_relative_to(root):
            raise ValueError("Frontend build manifest output must be outside the public root")
        info_text = args.build_info if args.build_info.lstrip().startswith("{") else Path(args.build_info).read_text(encoding="utf-8")
        build_info = json.loads(info_text, object_pairs_hook=_json_object)
        manifest = create_manifest(root, args.revision, build_info, args.mode)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"frontend manifest: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
