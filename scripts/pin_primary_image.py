"""Persist only an accepted immutable image ID in a retained private runtime env."""
import argparse
import os
from pathlib import Path
import re
import stat
import tempfile

from render_primary_environment import read_env


def pin_image(path: Path, image: str, revision: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full immutable image ID and release SHA required")
    if path.is_symlink() or path.name != ".env.primary" or path.parent.name != revision:
        raise ValueError("dedicated retained runtime environment required")
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError("runtime environment parent must be canonical")
    original = path.read_text(encoding="utf-8")
    values = read_env(path)
    if values.get("PU_RELEASE_REVISION") != revision or "PRIMARY_IMAGE" not in values:
        raise ValueError("runtime release identity mismatch")
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name != "nt" and (mode not in (0o600, 0o400) or path.stat().st_uid != os.geteuid()):
        raise ValueError("runtime file must be private and owned by the deploy account")
    updated = re.sub(r"(?m)^PRIMARY_IMAGE=.*$", "PRIMARY_IMAGE=" + image, original)
    if updated == original:
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".image-pin.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(updated)
        if os.name != "nt":
            temporary.chmod(mode)
        if path.read_text(encoding="utf-8") != original:
            raise ValueError("runtime changed concurrently; image pin refused")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    pin_image(args.env, args.image, args.revision)
    print("Retained image identity pinned; no secret or execution flags changed.")
