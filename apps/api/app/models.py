from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceLevel(str, Enum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"


class WorkplacePreference(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    ANY = "any"


class ProfileSalaryPeriod(str, Enum):
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "experience IN ('intern', 'junior', 'middle', 'senior', 'lead', 'unknown')",
            name="ck_user_profiles_experience",
        ),
        CheckConstraint(
            "workplace_preference IN ('remote', 'hybrid', 'onsite', 'any')",
            name="ck_user_profiles_workplace_preference",
        ),
        CheckConstraint(
            "salary_period IN ('month', 'year', 'unknown')",
            name="ck_user_profiles_salary_period",
        ),
        CheckConstraint(
            "(salary_min IS NULL AND salary_currency IS NULL AND salary_period = 'unknown') OR "
            "(salary_min > 0 AND salary_currency IS NOT NULL AND salary_period IN ('month', 'year'))",
            name="ck_user_profiles_salary_block",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    target_roles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]"
    )
    skills: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]"
    )
    experience: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ExperienceLevel.UNKNOWN.value,
        server_default=ExperienceLevel.UNKNOWN.value,
    )
    location: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]"
    )
    workplace_preference: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WorkplacePreference.ANY.value,
        server_default=WorkplacePreference.ANY.value,
    )
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProfileSalaryPeriod.UNKNOWN.value,
        server_default=ProfileSalaryPeriod.UNKNOWN.value,
    )
    languages: Mapped[list[dict[str, str]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProfileExperienceFact(Base):
    __tablename__ = "profile_experience_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(
        ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkExperience(Base):
    __tablename__ = "work_experiences"
    __table_args__ = (
        CheckConstraint("company IS NOT NULL OR position IS NOT NULL", name="ck_work_identity"),
        CheckConstraint("engagement_kind IN ('employment','internship','freelance','unknown')", name="ck_work_kind"),
        CheckConstraint("start_year IS NULL OR start_year BETWEEN 1900 AND 9999", name="ck_work_start_year"),
        CheckConstraint("end_year IS NULL OR end_year BETWEEN 1900 AND 9999", name="ck_work_end_year"),
        CheckConstraint("start_month IS NULL OR (start_year IS NOT NULL AND start_month BETWEEN 1 AND 12)", name="ck_work_start_month"),
        CheckConstraint("end_month IS NULL OR (end_year IS NOT NULL AND end_month BETWEEN 1 AND 12)", name="ck_work_end_month"),
        CheckConstraint("end_year IS NULL OR is_current IS FALSE", name="ck_work_current"),
        CheckConstraint("start_year IS NULL OR end_year IS NULL OR start_year < end_year OR (start_year = end_year AND coalesce(start_month,1) <= coalesce(end_month,12))", name="ck_work_order"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    company: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[str | None] = mapped_column(String(200))
    engagement_kind: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    start_year: Mapped[int | None]
    start_month: Mapped[int | None]
    end_year: Mapped[int | None]
    end_month: Mapped[int | None]
    is_current: Mapped[bool | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    RECRUITER_RESPONSE = "recruiter_response"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"


class JobSource(str, Enum):
    LINKEDIN = "linkedin"
    HH = "hh"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    COMPANY_SITE = "company_site"
    TRUDVSEM = "trudvsem"


class IngestionMethod(str, Enum):
    MANUAL = "manual"
    DISCOVER = "discover"


class ParsingStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class AIEnrichmentStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site', 'trudvsem')", name="ck_jobs_source"),
        CheckConstraint("ingestion_method IN ('manual', 'discover')", name="ck_jobs_ingestion_method"),
        CheckConstraint(
            "(external_id IS NULL AND source_scope IS NULL) OR "
            "(external_id IS NOT NULL AND source_scope IS NOT NULL)",
            name="ck_jobs_external_identity_block",
        ),
        UniqueConstraint(
            "source", "source_scope", "external_id", name="uq_jobs_external_identity"
        ),
        CheckConstraint("salary_period IN ('hour', 'day', 'week', 'month', 'year', 'unknown')", name="ck_jobs_salary_period"),
        CheckConstraint("workplace_type IN ('remote', 'hybrid', 'onsite', 'unknown')", name="ck_jobs_workplace_type"),
        CheckConstraint("employment_type IN ('full_time', 'part_time', 'contract', 'internship', 'temporary', 'unknown')", name="ck_jobs_employment_type"),
        CheckConstraint("parsing_status IN ('not_attempted', 'pending', 'success', 'partial', 'failed')", name="ck_jobs_parsing_status"),
        CheckConstraint("seniority IN ('intern', 'junior', 'middle', 'senior', 'lead', 'unknown')", name="ck_jobs_seniority"),
        CheckConstraint("ai_enrichment_status IN ('not_attempted', 'pending', 'success', 'failed')", name="ck_jobs_ai_enrichment_status"),
        CheckConstraint("ai_enrichment_error IS NULL OR ai_enrichment_error IN ('timeout', 'invalid_output', 'provider_error', 'processing_failed')", name="ck_jobs_ai_enrichment_error"),
        CheckConstraint("salary_min IS NULL OR salary_min >= 0", name="ck_jobs_salary_min_nonnegative"),
        CheckConstraint("salary_max IS NULL OR salary_max >= 0", name="ck_jobs_salary_max_nonnegative"),
        CheckConstraint("salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min", name="ck_jobs_salary_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    ingestion_method: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    source_scope: Mapped[str | None] = mapped_column(String(255))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(String(512))
    company: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String)
    requirements_text: Mapped[str | None] = mapped_column(Text)
    salary_text: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    salary_period_inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    location: Mapped[str | None] = mapped_column(String(512))
    workplace_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    employment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    parsing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_attempted", server_default="not_attempted")
    parsing_error: Mapped[str | None] = mapped_column(String(128))
    required_skills: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]")
    nice_to_have_skills: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]")
    experience_requirements: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]")
    language_requirements: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]")
    responsibilities: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list, server_default="[]")
    seniority: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    ai_enrichment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_attempted", server_default="not_attempted")
    ai_enrichment_error: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id"),
        CheckConstraint(
            "status IN ('saved', 'applied', 'recruiter_response', 'interview', 'offer', 'hired', 'withdrawn', 'rejected')",
            name="ck_applications_status",
        ),
        CheckConstraint(
            "(next_action IS NULL AND next_action_due_on IS NULL) OR "
            "(next_action IS NOT NULL AND next_action_due_on IS NOT NULL)",
            name="ck_applications_next_action_block",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=ApplicationStatus.SAVED.value, server_default=ApplicationStatus.SAVED.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"
    __table_args__ = (
        CheckConstraint(
            "status IN ('saved', 'applied', 'recruiter_response', 'interview', 'offer', 'hired', 'withdrawn', 'rejected')",
            name="ck_application_status_history_status",
        ),
        Index(
            "ix_application_status_history_application_occurred_id",
            "application_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApplicationMatchSnapshot(Base):
    __tablename__ = "application_match_snapshots"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_application_match_snapshots_application_id"),
        CheckConstraint(
            "snapshot_schema_version = 1",
            name="ck_application_match_snapshots_schema_version",
        ),
        CheckConstraint(
            "capture_status IN ('captured', 'unavailable')",
            name="ck_application_match_snapshots_capture_status",
        ),
        CheckConstraint(
            "unavailable_reason IS NULL OR unavailable_reason IN ('profile_missing', 'matcher_error')",
            name="ck_application_match_snapshots_unavailable_reason",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_application_match_snapshots_score",
        ),
        CheckConstraint(
            "coverage IS NULL OR coverage BETWEEN 0 AND 100",
            name="ck_application_match_snapshots_coverage",
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('insufficient_data', 'low', 'medium', 'high')",
            name="ck_application_match_snapshots_verdict",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name="ck_application_match_snapshots_confidence",
        ),
        CheckConstraint(
            "(capture_status = 'captured' AND unavailable_reason IS NULL "
            "AND algorithm_version IS NOT NULL AND verdict IS NOT NULL AND coverage IS NOT NULL "
            "AND profile_updated_at IS NOT NULL AND job_updated_at IS NOT NULL "
            "AND job_parsing_status IS NOT NULL AND job_ai_enrichment_status IS NOT NULL "
            "AND inputs IS NOT NULL AND result_detail IS NOT NULL) OR "
            "(capture_status = 'unavailable' AND unavailable_reason IS NOT NULL "
            "AND algorithm_version IS NULL AND score IS NULL AND verdict IS NULL "
            "AND coverage IS NULL AND confidence IS NULL AND profile_updated_at IS NULL "
            "AND job_updated_at IS NULL AND job_parsing_status IS NULL "
            "AND job_ai_enrichment_status IS NULL AND inputs IS NULL AND result_detail IS NULL)",
            name="ck_application_match_snapshots_capture_payload",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    trigger_status_history_id: Mapped[int] = mapped_column(
        ForeignKey("application_status_history.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_schema_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    capture_status: Mapped[str] = mapped_column(String(16), nullable=False)
    unavailable_reason: Mapped[str | None] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    algorithm_version: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[int | None]
    verdict: Mapped[str | None] = mapped_column(String(32))
    coverage: Mapped[int | None]
    confidence: Mapped[str | None] = mapped_column(String(16))

    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_parsing_status: Mapped[str | None] = mapped_column(String(16))
    job_ai_enrichment_status: Mapped[str | None] = mapped_column(String(16))

    inputs: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
    result_detail: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )
