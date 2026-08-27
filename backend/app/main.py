from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.history import router as history_router
from app.api.auth import router as auth_router
from app.api.tasks import router as tasks_router
from app.api.responses import router as responses_router
from app.api.telegram import router as telegram_router
from app.api.governance import router as governance_router
from app.api.dashboard import router as dashboard_router
from app.api.local_upload import router as local_upload_router
from app.api.workspace import recover_incomplete_analyses, recover_incomplete_snapshots, router as workspace_router

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
from app.core.readiness import readiness_report


app = FastAPI(
    title="PU Workspace",
    version="0.26.0",
)

STATIC_DIR = Path(__file__).with_name("static")
REACT_DIR = Path(__file__).with_name("react_dist")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(users_router, dependencies=[Depends(require_user)])
app.include_router(access_router, dependencies=[Depends(require_user)])
app.include_router(drive_router, dependencies=[Depends(require_user)])
app.include_router(documents_router, dependencies=[Depends(require_user)])
app.include_router(google_drive_router)
app.include_router(tasks_router)
app.include_router(responses_router)
app.include_router(telegram_router)
app.include_router(governance_router)
app.include_router(dashboard_router)
app.include_router(local_upload_router)
app.include_router(workspace_router)


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
    recover_incomplete_snapshots()
    recover_incomplete_analyses()


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status():
    return {"status": "ok", "service": "PU Workspace", "version": "0.26.0"}


@app.get("/api/readiness")
def readiness():
    report = readiness_report()
    return report


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }


if REACT_DIR.exists():
    app.mount("/new", StaticFiles(directory=REACT_DIR, html=True), name="react-ui")
