from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import ExperienceLevel, ProfileSalaryPeriod, WorkplacePreference
from app.schemas import LanguageOut
from app.work_experience_schema import WorkExperienceIn


class ImportProfile(BaseModel):
    target_roles: list[str]
    skills: list[str]
    experience: ExperienceLevel
    location: list[str]
    workplace_preference: WorkplacePreference
    salary_min: Decimal | None
    salary_currency: str | None
    salary_period: ProfileSalaryPeriod
    languages: list[LanguageOut]


class ImportPreview(BaseModel):
    token: str
    revision: int = Field(default=1, ge=1)
    expires_at: float
    current: ImportProfile | None
    proposed: ImportProfile
    work_experience_mode: Literal["replace"]
    current_work_experience_count: int = Field(ge=0)
    experience_facts_mode: Literal["replace"]
    current_experience_fact_count: int = Field(ge=0)
    work_experience: list[WorkExperienceIn]
    experience_facts: list[str]


class ImportAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    revision: int = Field(default=1, ge=1, strict=True)


class EditedProfile(ImportProfile):
    model_config = ConfigDict(extra="forbid")


class ProfileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["profile"]
    value: EditedProfile


class WorkEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["work"]
    index: int = Field(ge=0, le=19, strict=True)
    value: WorkExperienceIn


class WorkDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["delete_work"]
    index: int = Field(ge=0, le=19, strict=True)


class FactEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fact"]
    index: int = Field(ge=0, le=19, strict=True)
    text: str = Field(min_length=1, max_length=500, strict=True)


class FactDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["delete_fact"]
    index: int = Field(ge=0, le=19, strict=True)


class ImportEdit(ImportAction):
    revision: int = Field(ge=1, strict=True)
    edit: Annotated[ProfileEdit | WorkEdit | WorkDelete | FactEdit | FactDelete, Field(discriminator="kind")]
