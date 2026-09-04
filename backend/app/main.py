from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api.history import router as history_router
from app.api.auth import router as auth_router
from app.api.tasks import router as tasks_router
from app.api.responses import router as responses_router
from app.api.telegram import router as telegram_router
from app.api.governance import router as governance_router
from app.api.dashboard import router as dashboard_router
from app.api.local_upload import router as local_upload_router
from app.api.workspace import router as workspace_router
from app.api.organizations_contracts import router as organizations_contracts_router
from app.api.contract_discovery import router as contract_discovery_router
from app.api.contract_package import router as contract_package_router
from app.api.ai_secretary import router as ai_secretary_router
from app.api.management import router as management_router
from app.api.execution_finance import router as execution_finance_router
from app.api.gmail import router as gmail_router
from app.api.ai_policy import router as ai_policy_router
from app.api.analytics import router as analytics_router
from app.api.integrations import router as integrations_router
from app.api.project_contacts import router as project_contacts_router
from app.api.mail import router as mail_router

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
from app.core.auth import cleanup_expired_sessions, require_user
from app.database import SessionLocal
from app.core.readiness import readiness_report
from app.core.observability import observe_request


APP_VERSION = "1.0.3"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db = SessionLocal()
    try:
        cleanup_expired_sessions(db)
    finally:
        db.close()
    # Background work is executed by durable worker/scheduler services. Keeping
    # API startup side-effect free allows multiple API processes to run safely.
    yield


app = FastAPI(
    title="PU Workspace",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.middleware("http")(observe_request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

STATIC_DIR = Path(__file__).with_name("static")
REACT_DIR = Path(__file__).with_name("react_dist")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(users_router, dependencies=[Depends(require_user)])
app.include_router(access_router, dependencies=[Depends(require_user)])
app.include_router(drive_router, dependencies=[Depends(require_user)])
app.include_router(documents_router, dependencies=[Depends(require_user)])
app.include_router(organizations_contracts_router)
app.include_router(contract_discovery_router)
app.include_router(contract_package_router)
app.include_router(ai_secretary_router)
app.include_router(management_router)
app.include_router(execution_finance_router)
app.include_router(gmail_router)
app.include_router(ai_policy_router)
app.include_router(analytics_router)
app.include_router(integrations_router)
app.include_router(project_contacts_router)
app.include_router(mail_router)
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


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/.well-known/assetlinks.json", include_in_schema=False)
def android_asset_links():
    fingerprint = os.getenv("PU_ANDROID_CERT_SHA256", "").strip().upper()
    if not fingerprint:
        return JSONResponse(
            {"detail": "Android release certificate is not configured"},
            status_code=503,
        )
    return [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "ru.puworkspace.app",
            "sha256_cert_fingerprints": [fingerprint],
        },
    }]


@app.get("/api/status")
def api_status():
    return {
        "status": "ok",
        "service": "PU Workspace",
        "version": APP_VERSION,
        "release": os.getenv("PU_RELEASE_REVISION", "development"),
    }


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
