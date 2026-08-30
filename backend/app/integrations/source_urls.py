from __future__ import annotations


def source_object_url(provider: str, external_id: str | None) -> str | None:
    """Resolve an adapter-owned object URL so Core clients never construct vendor URLs."""
    normalized = provider.strip().lower()
    if not external_id:
        return None
    if normalized in {"google_drive", "google_workspace"}:
        return f"https://drive.google.com/open?id={external_id}"
    return None
