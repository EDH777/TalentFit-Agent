import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.job_categories import JobCategory


class ManualCandidateCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    job_category: JobCategory | None = None
    years_experience: str = ""
    tags: list[str] = Field(default_factory=list)
    raw_text: str = Field(min_length=1, max_length=200000)


class CandidateUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    expected_updated_at: datetime | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    job_category: JobCategory | None = None
    years_experience: str | None = None
    tags: list[str] | None = None
    raw_text: str | None = Field(default=None, min_length=1)

    @field_validator("name", "raw_text")
    @classmethod
    def required_when_present(cls, value):
        if value is None:
            raise ValueError("姓名和简历正文不可为空")
        return value


class CandidateOut(BaseModel):
    id: int
    name: str
    job_category: JobCategory | None = None
    years_experience: str
    tags: list[str]
    source_filename: str | None
    status: str
    enabled: bool = True
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, candidate: Any) -> "CandidateOut":
        return cls(
            id=candidate.id,
            name=candidate.name,
            job_category=candidate.job_category,
            years_experience=candidate.years_experience,
            tags=json.loads(candidate.tags_json or "[]"),
            source_filename=candidate.source_filename,
            status=candidate.status,
            enabled=candidate.enabled,
            error_message=candidate.error_message,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )


class CandidateDetail(CandidateOut):
    raw_text: str | None = None

    @classmethod
    def from_model(cls, candidate: Any) -> "CandidateDetail":
        base = CandidateOut.from_model(candidate).model_dump()
        return cls(**base, raw_text=None if candidate.source_filename or candidate.file_path else candidate.raw_text)


class SummaryOut(BaseModel):
    total: int
    ready: int
    processing: int
    failed: int
