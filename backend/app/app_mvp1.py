"""Standalone MVP-1 ASGI composition.

Run with ``uvicorn app.app_mvp1:app``.  This module deliberately does not
import ``app.main`` or the aggregate ``app.models`` package, so later MVP
routers and model bundles are not initialized as a side effect.
"""

from contextlib import asynccontextmanager
import os

# Must precede every app.models.* import performed by router modules.
os.environ["PU_MODEL_SCOPE"] = "mvp1"
os.environ["PU_MVP1_OPTIONAL_EXTENSIONS"] = "0"

from fastapi import Depends, FastAPI

from app.api.access import router as access_router
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.drive import router as drive_router
from app.api.history import router as history_router
from app.api.mvp1_google_oauth import router as mvp1_google_oauth_router
from app.api.organizations_contracts import router as organizations_contracts_router
from app.api.projects import router as projects_router
from app.api.users import router as users_router
from app.api.workspace import router as workspace_router
from app.core.auth import cleanup_expired_sessions, require_user
from app.core.mvp1_readiness import readiness_report
from app.database import SessionLocal
from app.organizer import resources_router, router as organizer_router


MVP1_EXCLUDED_CONTRACT_ENDPOINTS = frozenset({
    "analyze_contract",             # MVP-2 Tasks/Governance/finance
    "initialize_contract_control",  # MVP-3 execution finance
})


@asynccontextmanager
async def lifespan(_: FastAPI):
    db = SessionLocal()
    try:
        cleanup_expired_sessions(db)
    finally:
        db.close()
    yield


app = FastAPI(title="PU Workspace MVP-1", version="1.0.0-mvp1", lifespan=lifespan)

# MVP-1 ONLY: identity, tenants/projects, source references, snapshots,
# virtual documents, proposals/change-batches, rules, rollback and audit.
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(users_router, dependencies=[Depends(require_user)])
app.include_router(access_router, dependencies=[Depends(require_user)])
app.include_router(drive_router, dependencies=[Depends(require_user)])
app.include_router(mvp1_google_oauth_router)
app.include_router(documents_router, dependencies=[Depends(require_user)])

# The contracts module is shared with the legacy application.  Only its MVP-1
# organization/contract routes are composed here; later finance endpoints stay
# in the repository but are not exposed by this ASGI app.
for _route in organizations_contracts_router.routes:
    if getattr(_route, "name", None) not in MVP1_EXCLUDED_CONTRACT_ENDPOINTS:
        app.router.routes.append(_route)

app.include_router(workspace_router)
app.include_router(history_router, dependencies=[Depends(require_user)])
app.include_router(organizer_router, dependencies=[Depends(require_user)])
app.include_router(resources_router, dependencies=[Depends(require_user)])


@app.get("/health")
def health():
    return {"status": "healthy", "scope": "mvp1"}


@app.get("/api/readiness")
def readiness():
    return readiness_report()
