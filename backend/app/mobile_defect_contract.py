"""Typed metadata-only defect creation. Codex fallback; photos use their own API."""
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from app.api.defects import DefectBody


class OfflineDefectData(DefectBody):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    object_name: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    responsible: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Defect title required")
        return value


class OfflineDefectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_mutation_id: str = Field(min_length=8, max_length=90, pattern=r"^[A-Za-z0-9_.:-]+$")
    device_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    project_id: int = Field(strict=True, gt=0)
    operation: Literal["defect.create"]
    client_created_at: AwareDatetime
    data: OfflineDefectData
