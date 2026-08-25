from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.history import router as history_router
from app.api.auth import router as auth_router

from app.api.access import router as access_router
from app.api.documents import router as documents_router
from app.api.drive import router as drive_router
from app.api.google_drive import router as google_drive_router
from app.api.projects import router as projects_router
from app.api.users import router as users_router
from app.models import (
    Document,
    DriveConnection,
    GoogleOAuthToken,
    Project,
    ProjectMember,
    User,
)
from app.organizer import router as organizer_router
from app.organizer import recover_incomplete_scans
from app.core.auth import cleanup_expired_sessions, require_user
from app.database import SessionLocal


app = FastAPI(
    title="PU Workspace",
    version="0.4.0",
)

STATIC_DIR = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(users_router, dependencies=[Depends(require_user)])
app.include_router(access_router, dependencies=[Depends(require_user)])
app.include_router(drive_router, dependencies=[Depends(require_user)])
app.include_router(documents_router, dependencies=[Depends(require_user)])
app.include_router(google_drive_router)


app.include_router(history_router, dependencies=[Depends(require_user)])
app.include_router(organizer_router, dependencies=[Depends(require_user)])


@app.on_event("startup")
def recover_organizer_jobs():
    db = SessionLocal()
    try:
        cleanup_expired_sessions(db)
    finally:
        db.close()
    recover_incomplete_scans()


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status():
    return {"status": "ok", "service": "PU Workspace", "version": "0.4.0"}


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }
