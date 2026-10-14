"""Google Drive operations for off-site DB backups.

Runs INSIDE the backend container (it reuses the app's own Google client and
the owner's stored OAuth token). Invoked by offsite_backup.sh/restore_check.sh:

    python drive_ops.py upload <file> <name> <size> <md5> <counts_json>
    python drive_ops.py rotate
    python drive_ops.py latest
    python drive_ops.py download <file_id>      # bytes to stdout

Configuration (env): PU_BACKUP_GOOGLE_TOKEN_ID, PU_BACKUP_DRIVE_FOLDER,
PU_BACKUP_KEEP_DAILY/WEEKLY/MONTHLY. Prints only metadata, never contents.
"""

import json
import os
import sys

# Keep stdout clean for binary downloads: everything else goes to stderr.
BINARY_OUT = sys.stdout.buffer
sys.stdout = sys.stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.environ.get("PU_BACKUP_APP_DIR", "/app"))

from rotation import NAME_RE, select_delete  # noqa: E402

FOLDER_MIME = "application/vnd.google-apps.folder"
TOKEN_ID = int(os.environ.get("PU_BACKUP_GOOGLE_TOKEN_ID", "11"))
FOLDER_NAME = os.environ.get("PU_BACKUP_DRIVE_FOLDER", "PU Workspace — резервные копии БД")


def emit(payload: dict) -> None:
    BINARY_OUT.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
    BINARY_OUT.flush()


def drive():
    from app.database import SessionLocal
    from app.integrations.google_workspace import google_workspace_for_mailbox

    db = SessionLocal()
    return google_workspace_for_mailbox(TOKEN_ID, db).service("drive", "v3")


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def folder_id(service, create: bool) -> str | None:
    found = service.files().list(
        q=f"name = '{_q(FOLDER_NAME)}' and mimeType = '{FOLDER_MIME}' and trashed = false and 'me' in owners",
        fields="files(id, createdTime)",
        orderBy="createdTime",
        spaces="drive",
    ).execute().get("files", [])
    if found:
        return found[0]["id"]
    if not create:
        return None
    return service.files().create(
        body={"name": FOLDER_NAME, "mimeType": FOLDER_MIME}, fields="id"
    ).execute()["id"]


def list_backups(service, parent: str) -> list[dict]:
    files, token = [], None
    while True:
        page = service.files().list(
            q=f"'{parent}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, size, md5Checksum, appProperties)",
            pageSize=200,
            pageToken=token,
        ).execute()
        files.extend(f for f in page.get("files", []) if NAME_RE.match(f["name"]))
        token = page.get("nextPageToken")
        if not token:
            return files


def upload(path: str, name: str, size: str, md5: str, counts_json: str) -> int:
    from googleapiclient.http import MediaFileUpload

    service = drive()
    parent = folder_id(service, create=True)
    counts = json.loads(counts_json or "{}")
    body = {
        "name": name,
        "parents": [parent],
        "description": "PU Workspace PostgreSQL dump (pg_dump -Fc), GPG AES256 symmetric.",
        "appProperties": {f"rows_{k}": str(v) for k, v in counts.items()},
    }
    media = MediaFileUpload(path, mimetype="application/octet-stream", resumable=True, chunksize=8 * 1024 * 1024)
    created = service.files().create(body=body, media_body=media, fields="id").execute()
    meta = service.files().get(fileId=created["id"], fields="id, name, size, md5Checksum").execute()
    if meta.get("size") != size or meta.get("md5Checksum") != md5:
        service.files().delete(fileId=created["id"]).execute()
        print(f"verify failed: drive size={meta.get('size')} md5={meta.get('md5Checksum')}")
        return 2
    emit({"id": meta["id"], "name": meta["name"], "size": int(meta["size"]), "md5": meta["md5Checksum"]})
    return 0


def rotate() -> int:
    service = drive()
    parent = folder_id(service, create=False)
    if parent is None:
        print("backup folder not found")
        return 2
    files = list_backups(service, parent)
    doomed = set(select_delete(
        [f["name"] for f in files],
        int(os.environ.get("PU_BACKUP_KEEP_DAILY", "7")),
        int(os.environ.get("PU_BACKUP_KEEP_WEEKLY", "4")),
        int(os.environ.get("PU_BACKUP_KEEP_MONTHLY", "6")),
    ))
    for item in files:
        if item["name"] in doomed:
            # Trash, not hard delete: Drive purges trash after 30 days, which
            # leaves a grace window if retention ever misbehaves.
            service.files().update(fileId=item["id"], body={"trashed": True}).execute()
    emit({"kept": len(files) - len(doomed), "trashed": sorted(doomed)})
    return 0


def latest() -> int:
    service = drive()
    parent = folder_id(service, create=False)
    files = list_backups(service, parent) if parent else []
    if not files:
        print("no backups found")
        return 2
    newest = max(files, key=lambda f: f["name"])
    emit({
        "id": newest["id"],
        "name": newest["name"],
        "size": int(newest.get("size", 0)),
        "md5": newest.get("md5Checksum", ""),
        "counts": {k[5:]: int(v) for k, v in (newest.get("appProperties") or {}).items() if k.startswith("rows_")},
    })
    return 0


def download(file_id: str) -> int:
    from googleapiclient.http import MediaIoBaseDownload

    request = drive().files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(BINARY_OUT, request, chunksize=8 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    BINARY_OUT.flush()
    return 0


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    handlers = {"upload": upload, "rotate": rotate, "latest": latest, "download": download}
    if command not in handlers:
        print(f"unknown command: {command}")
        return 64
    return handlers[command](*args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
