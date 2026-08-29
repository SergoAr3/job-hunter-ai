from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint, func
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


class ApplicationStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class JobSource(str, Enum):
    LINKEDIN = "linkedin"
    HH = "hh"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    COMPANY_SITE = "company_site"


class IngestionMethod(str, Enum):
    MANUAL = "manual"


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
        CheckConstraint("source IN ('linkedin', 'hh', 'greenhouse', 'lever', 'company_site')", name="ck_jobs_source"),
        CheckConstraint("ingestion_method IN ('manual')", name="ck_jobs_ingestion_method"),
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
            "status IN ('saved', 'applied', 'interview', 'offer', 'rejected')",
            name="ck_applications_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=ApplicationStatus.SAVED.value, server_default=ApplicationStatus.SAVED.value
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
