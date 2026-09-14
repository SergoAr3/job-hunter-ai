"""Work history input keeps the precision and uncertainty supplied by the user."""
import unicodedata
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkExperienceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str | None = Field(default=None, max_length=200, strict=True)
    position: str | None = Field(default=None, max_length=200, strict=True)
    engagement_kind: Literal["employment", "internship", "freelance", "unknown"] = "unknown"
    start_year: int | None = Field(default=None, ge=1900, le=9999, strict=True)
    start_month: int | None = Field(default=None, ge=1, le=12, strict=True)
    end_year: int | None = Field(default=None, ge=1900, le=9999, strict=True)
    end_month: int | None = Field(default=None, ge=1, le=12, strict=True)
    is_current: bool | None = Field(default=None, strict=True)

    @field_validator("company", "position", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
                raise ValueError("control characters are not allowed")
            return " ".join(value.split()) or None
        return value

    @model_validator(mode="after")
    def validate_period(self):
        if not self.company and not self.position:
            raise ValueError("company or position is required")
        today = date.today()
        for year, month in ((self.start_year, self.start_month), (self.end_year, self.end_month)):
            if month is not None and year is None:
                raise ValueError("month requires year")
            if year is not None and (year > today.year or (year == today.year and month and month > today.month)):
                raise ValueError("future dates are not allowed")
        if self.end_year is not None and self.is_current is not False:
            raise ValueError("end date requires explicitly ended employment")
        if self.start_year and self.end_year:
            if (self.start_year, self.start_month or 1) > (self.end_year, self.end_month or 12):
                raise ValueError("end precedes start")
        return self


class WorkExperienceOut(WorkExperienceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
    duration_months: int | None = None


class WorkExperiencesOut(BaseModel):
    items: list[WorkExperienceOut]
