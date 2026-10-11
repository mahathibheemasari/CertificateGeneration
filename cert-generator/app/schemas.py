from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app import config


class RecipientIn(BaseModel):
    """Validation rules for ONE recipient. Applied per row, not to the whole request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    achievement: str | None = Field(default=None, max_length=150)


class JobCreate(BaseModel):
    """Request-level validation. Recipients are kept as raw items on purpose: one bad
    row must not reject the whole request (see services.validate_recipients)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=150)
    issuer: str = Field(min_length=1, max_length=150)
    issue_date: date | None = None
    recipients: list[Any] = Field(min_length=1, max_length=config.MAX_RECIPIENTS_PER_JOB)


class FailureOut(BaseModel):
    certificate_id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    event_name: str
    issuer: str
    issue_date: date
    status: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    download_all_url: str | None
    failures: list[FailureOut]
    failures_truncated: bool

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        """All timestamps are stored in UTC, but SQLite returns them without tzinfo."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class CertificateOut(BaseModel):
    id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    download_url: str | None


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
