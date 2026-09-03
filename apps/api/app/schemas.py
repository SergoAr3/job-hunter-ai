from datetime import datetime
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, ValidationInfo, field_validator, model_validator

from app.models import ApplicationStatus, ExperienceLevel, ProfileSalaryPeriod, WorkplacePreference
from app.services.profile_normalization import (
    is_workplace_like_location,
    normalize_profile_language_level,
    normalize_profile_language_name,
    normalize_profile_skills,
    profile_language_name_key,
)
from app.services.salary_validation import is_iso_4217_currency

http_url_adapter = TypeAdapter(AnyHttpUrl)


class TelegramUserIn(BaseModel):
    telegram_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=255)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    language_code: str | None = Field(default=None, max_length=16)


class TelegramUserOut(BaseModel):
    id: int
    telegram_id: int
    created: bool


MAX_PROFILE_ITEMS = 30
MAX_PROFILE_ITEM_LENGTH = 100


class LanguageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(min_length=1, max_length=100)
    level: str = Field(min_length=1, max_length=32)

    @field_validator("language", "level")
    @classmethod
    def normalize_value(cls, value: str, info: ValidationInfo) -> str:
        if info.field_name == "language":
            return normalize_profile_language_name(value)
        return normalize_profile_language_level(value)


class LanguageOut(BaseModel):
    """Preserve the established response shape for profiles saved before strict validation."""

    language: str
    level: str


def validate_unique_profile_languages(values: list[LanguageIn]) -> list[LanguageIn]:
    seen: set[str] = set()
    for item in values:
        key = profile_language_name_key(item.language)
        if key in seen:
            raise ValueError("languages must not contain duplicate language names")
        seen.add(key)
    return values


class ProfileLanguagesNormalizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: list[LanguageIn] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)

    @field_validator("languages")
    @classmethod
    def validate_unique_languages(cls, values: list[LanguageIn]) -> list[LanguageIn]:
        return validate_unique_profile_languages(values)


class ProfileSkillsNormalizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        normalized = normalize_profile_skills(values)
        for item in normalized:
            if len(item) > MAX_PROFILE_ITEM_LENGTH:
                raise ValueError(f"list items must not exceed {MAX_PROFILE_ITEM_LENGTH} characters")
        return normalized


class UserProfilePutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_roles: list[str] = Field(min_length=1, max_length=MAX_PROFILE_ITEMS)
    skills: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)
    experience: ExperienceLevel = ExperienceLevel.UNKNOWN
    location: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)
    workplace_preference: WorkplacePreference = WorkplacePreference.ANY
    salary_min: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    salary_period: ProfileSalaryPeriod = ProfileSalaryPeriod.UNKNOWN
    languages: list[LanguageIn] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)

    @field_validator("target_roles", "location")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("list items must not be blank")
            if len(item) > MAX_PROFILE_ITEM_LENGTH:
                raise ValueError(f"list items must not exceed {MAX_PROFILE_ITEM_LENGTH} characters")
            normalized.append(item)
        return normalized

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        normalized = normalize_profile_skills(values)
        for item in normalized:
            if len(item) > MAX_PROFILE_ITEM_LENGTH:
                raise ValueError(f"list items must not exceed {MAX_PROFILE_ITEM_LENGTH} characters")
        return normalized

    @field_validator("location")
    @classmethod
    def reject_workplace_values_as_locations(cls, values: list[str]) -> list[str]:
        if any(is_workplace_like_location(value) for value in values):
            raise ValueError("location must contain geographic locations, not workplace preferences")
        return values

    @field_validator("languages")
    @classmethod
    def validate_unique_languages(cls, values: list[LanguageIn]) -> list[LanguageIn]:
        return validate_unique_profile_languages(values)

    @field_validator("salary_currency")
    @classmethod
    def validate_salary_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if not is_iso_4217_currency(normalized):
            raise ValueError("salary_currency must be an active ISO 4217 code")
        return normalized

    @model_validator(mode="after")
    def validate_salary_block(self) -> "UserProfilePutIn":
        if self.salary_min is None:
            if self.salary_currency is not None or self.salary_period != ProfileSalaryPeriod.UNKNOWN:
                raise ValueError("salary must be fully absent or contain amount, currency, and month/year")
        elif self.salary_currency is None or self.salary_period == ProfileSalaryPeriod.UNKNOWN:
            raise ValueError("salary must contain amount, currency, and month/year")
        return self


class UserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    target_roles: list[str]
    skills: list[str]
    experience: ExperienceLevel
    location: list[str]
    workplace_preference: WorkplacePreference
    salary_min: Decimal | None
    salary_currency: str | None
    salary_period: ProfileSalaryPeriod
    languages: list[LanguageOut]
    created_at: datetime
    updated_at: datetime


class ApplicationCreateIn(BaseModel):
    source_url: str = Field(min_length=1, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        try:
            http_url_adapter.validate_python(value)
            parsed = urlsplit(value)
        except (ValidationError, ValueError) as error:
            raise ValueError("source_url must be a valid URL") from error
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source_url must be an absolute http or https URL")
        return value


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    ingestion_method: str
    source_url: str
    title: str | None
    company: str | None
    description: str | None
    requirements_text: str | None
    salary_text: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str
    salary_period_inferred: bool
    location: str | None
    workplace_type: str
    employment_type: str
    parsing_status: str
    parsing_error: str | None
    required_skills: list[str]
    nice_to_have_skills: list[str]
    experience_requirements: list[str]
    language_requirements: list[str]
    responsibilities: list[str]
    seniority: str
    ai_enrichment_status: str
    ai_enrichment_error: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    job_id: int
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime


class SavedApplicationOut(BaseModel):
    job: JobOut
    application: ApplicationOut
    job_created: bool
    application_created: bool


class ApplicationListItemOut(BaseModel):
    """Small application representation intended for the Telegram list."""

    app_id: int
    job_id: int
    created_at: datetime
    title: str | None
    company: str | None
    location: str | None
    workplace_type: str
    parsing_status: str
    ai_enrichment_status: str


class ApplicationsPageOut(BaseModel):
    items: list[ApplicationListItemOut]
    has_next: bool


class ApplicationDetailOut(BaseModel):
    application: ApplicationOut
    job: JobOut


class MatchComponentOut(BaseModel):
    weight: int
    score: int | None
    status: str
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class MatchReasonOut(BaseModel):
    code: str
    component: str
    value: str | None = None


class MatchInputStateOut(BaseModel):
    profile_updated_at: datetime | None
    job_updated_at: datetime | None
    parsing_status: str
    ai_enrichment_status: str


class MatchResultOut(BaseModel):
    algorithm_version: str
    application_id: int
    job_id: int
    score: int | None
    verdict: str
    coverage: int
    input_state: MatchInputStateOut
    components: dict[str, MatchComponentOut]
    strengths: list[MatchReasonOut]
    gaps: list[MatchReasonOut]
    conflicts: list[MatchReasonOut]
