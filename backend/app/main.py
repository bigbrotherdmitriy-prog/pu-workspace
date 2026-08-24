from fastapi import FastAPI
from app.api.history import router as history_router

from app.api.access import router as access_router
from app.api.documents import router as documents_router
from app.api.drive import router as drive_router
from app.api.google_drive import router as google_drive_router
from app.api.projects import router as projects_router
from app.api.users import router as users_router
from app.database import Base, engine
from app.models import (
    Document,
    DriveConnection,
    GoogleOAuthToken,
    Project,
    ProjectMember,
    User,
)
from app.organizer import router as organizer_router


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PU Workspace",
    version="0.4.0",
)

app.include_router(projects_router)
app.include_router(users_router)
app.include_router(access_router)
app.include_router(drive_router)
app.include_router(documents_router)
app.include_router(google_drive_router)


app.include_router(history_router)
app.include_router(organizer_router)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "PU Workspace",
        "version": "0.4.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }
