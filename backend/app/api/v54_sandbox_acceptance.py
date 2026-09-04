"""Explicitly injected, test-database-only v5.4 product acceptance entry point.

The product never installs a runner.  A synthetic CI harness must opt in with
both an environment flag and a dependency override.  The endpoint therefore
cannot authorize a live provider or make the inactive pilot available in an
ordinary application process.
"""
from __future__ import annotations

import os
from typing import Literal, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.database import get_db
from app.models.user import User


router = APIRouter(prefix="/api/v54/sandbox", tags=["v54-synthetic-acceptance"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StageResult(_StrictModel):
    state: StrictStr
    revision: StrictInt | None = None
    count: StrictInt | None = None
    mode: StrictStr | None = None
    authorization_origin: StrictStr | None = None
    first_outcome: StrictStr | None = None
    final_outcome: StrictStr | None = None
    direct_undo_possible: StrictBool | None = None


class AcceptanceRequest(_StrictModel):
    scenario: Literal["mvp5-communication-to-action"]
    fault: Literal["timeout_after_effect"] = "timeout_after_effect"


class AcceptanceResult(_StrictModel):
    schema_name: Literal["puw.v54.product-acceptance.v1"]
    status: Literal["PASS"]
    synthetic_only: Literal[True]
    project_id: StrictInt
    context: StageResult
    evidence: StageResult
    deadline: StageResult
    internal_action: StageResult
    external_action: StageResult
    compensation: StageResult
    ledger: StageResult
    raw_content_published: Literal[False]


class SyntheticAcceptanceRuntime(Protocol):
    def run(self, *, scenario: str, fault: str, user_id: int) -> AcceptanceResult: ...


def get_synthetic_acceptance_runtime() -> SyntheticAcceptanceRuntime | None:
    """No production composition exists; tests must override this dependency."""
    return None


def _test_database(db: Session) -> bool:
    bind = db.get_bind()
    backend = bind.url.get_backend_name()
    database = bind.url.database or ""
    return backend == "sqlite" or (
        backend == "postgresql" and database.startswith("puw_v54_test_")
    )


@router.post("/acceptance", response_model=AcceptanceResult)
def run_acceptance(
    command: AcceptanceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    runtime: SyntheticAcceptanceRuntime | None = Depends(get_synthetic_acceptance_runtime),
    acceptance_header: str | None = Header(default=None, alias="X-PU-V54-Synthetic-Acceptance"),
):
    enabled = os.getenv("PU_V54_SYNTHETIC_ACCEPTANCE", "false").strip().lower() == "true"
    if not enabled or acceptance_header != "synthetic-v1" or runtime is None or not _test_database(db):
        raise HTTPException(404, "Not found")
    try:
        return runtime.run(scenario=command.scenario, fault=command.fault, user_id=user.id)
    except Exception as exc:
        # Never expose provider/database/source exception text at this boundary.
        raise HTTPException(409, "synthetic_acceptance_failed") from exc
