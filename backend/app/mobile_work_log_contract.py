"""Offline journal-create envelope; HTTP adapter lives in api.mobile_sync.

Codex fallback. Existing work_log service remains authoritative for business data.
"""
import re
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from app.api.work_log import WorkLogBody


class OfflineWorkLogData(WorkLogBody):
    model_config = ConfigDict(extra='forbid')
    work_date: str = Field(min_length=10, max_length=10)

    @field_validator('work_date')
    @classmethod
    def explicit_calendar_date(cls, value: str) -> str:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError('Explicit YYYY-MM-DD work_date required')
        date.fromisoformat(value)
        return value


class OfflineWorkLogCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_mutation_id: str = Field(min_length=8, max_length=90, pattern=r'^[A-Za-z0-9_.:-]+$')
    device_id: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    project_id: int = Field(strict=True, gt=0)
    operation: Literal['work_log.create']
    client_created_at: AwareDatetime
    data: OfflineWorkLogData
