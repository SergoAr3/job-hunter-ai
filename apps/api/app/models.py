from datetime import date, datetime
from uuid import UUID
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, PrimaryKeyConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email_canonical", name="uq_users_email_canonical"),
        CheckConstraint(
            "(email IS NULL AND email_canonical IS NULL AND password_hash IS NULL) OR "
            "(email IS NOT NULL AND email_canonical IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_users_email_credentials_complete",
        ),
        CheckConstraint(
            "telegram_id IS NOT NULL OR "
            "(email IS NOT NULL AND email_canonical IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_users_identity_present",
        ),
        CheckConstraint(
            "email IS NULL OR (email_canonical = lower(trim(email)) "
            "AND length(trim(email)) > 0 AND length(trim(password_hash)) > 0)",
            name="ck_users_email_canonical",
        ),
        CheckConstraint(
            "email_verified_at IS NULL OR email IS NOT NULL",
            name="ck_users_email_verification_identity",
        ),
        # Printable ASCII keeps lower(trim(email)) identical across databases.
        # SMTPUTF8 addresses require a separate, explicit normalization policy.
        CheckConstraint(
            "email IS NULL OR (instr(email, char(0)) = 0 "
            "AND instr(email_canonical, char(0)) = 0 AND email NOT GLOB '*[^ -~]*')",
            name="ck_users_email_ascii",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            "email IS NULL OR email !~ '[^ -~]'",
            name="ck_users_email_ascii",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(320))
    email_canonical: Mapped[str | None] = mapped_column(String(320))
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry"),
        CheckConstraint("last_seen_at >= created_at", name="ck_auth_sessions_last_seen"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthEmailToken(Base):
    __tablename__ = "auth_email_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify','reset')", name="ck_auth_email_tokens_purpose"),
        CheckConstraint("expires_at > created_at", name="ck_auth_email_tokens_expiry"),
        CheckConstraint("(consumed_at IS NULL AND outcome IS NULL) OR (consumed_at IS NOT NULL AND outcome IS NOT NULL AND outcome IN ('consumed','replaced'))", name="ck_auth_email_tokens_terminal"),
        Index("ix_auth_email_tokens_user_purpose", "user_id", "purpose"),
    )
    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(8))


class TelegramChallenge(Base):
    __tablename__ = "auth_telegram_challenges"
    __table_args__ = (
        CheckConstraint("purpose IN ('login','link')", name="ck_telegram_challenge_purpose"),
        CheckConstraint("(purpose = 'login' AND initiating_user_id IS NULL AND initiating_session_hash IS NULL) OR (purpose = 'link' AND initiating_user_id IS NOT NULL AND initiating_session_hash IS NOT NULL)", name="ck_telegram_challenge_initiator"),
        CheckConstraint("expires_at > created_at", name="ck_telegram_challenge_expiry"),
        CheckConstraint("(approved_at IS NULL AND approved_telegram_id IS NULL AND telegram_metadata IS NULL) OR (approved_at IS NOT NULL AND approved_telegram_id IS NOT NULL AND telegram_metadata IS NOT NULL)", name="ck_telegram_challenge_approval"),
        CheckConstraint("(consumed_at IS NULL AND outcome IS NULL) OR (consumed_at IS NOT NULL AND outcome IS NOT NULL AND outcome IN ('completed','conflict','cancelled'))", name="ck_telegram_challenge_outcome"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    initiating_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    initiating_session_hash: Mapped[str | None] = mapped_column(ForeignKey("auth_sessions.token_hash", ondelete="CASCADE"))
    approved_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_metadata: Mapped[dict | None] = mapped_column(JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


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
            "next_action_due_on IS NULL OR next_action IS NOT NULL",
            name="ck_applications_next_action_block",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminder: Mapped["ApplicationReminder | None"] = relationship(uselist=False, passive_deletes="all")

    @property
    def next_action_remind_at(self):
        return self.reminder.remind_at if self.reminder else None

    @property
    def next_action_timezone(self):
        return self.reminder.timezone if self.reminder else None

    @property
    def reminder_delivery_state(self):
        return self.reminder.delivery_state if self.reminder else None

    @property
    def reminder_sent_at(self):
        return self.reminder.sent_at if self.reminder else None

    @property
    def reminder_failure_reason(self):
        if not self.reminder or self.reminder.delivery_state != "failed":
            return None
        return {"DELIVERY_UNCERTAIN": "uncertain", "TELEGRAM_NOT_CONNECTED": "telegram_not_connected"}.get(self.reminder.last_error_code, "unavailable")

    @property
    def next_action_suggestions(self):
        from app.services.action_suggestions import suggestions_for
        return suggestions_for(self.status)
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


class ApplicationReminder(Base):
    __tablename__ = "application_reminders"
    __table_args__ = (
        CheckConstraint("delivery_state IN ('pending','claimed','sent','failed')", name="ck_reminders_state"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_reminders_attempts"),
        CheckConstraint("(delivery_state = 'claimed' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (delivery_state <> 'claimed' AND lease_token IS NULL AND lease_until IS NULL)", name="ck_reminders_lease"),
        CheckConstraint("(delivery_state = 'sent' AND sent_at IS NOT NULL) OR (delivery_state <> 'sent' AND sent_at IS NULL)", name="ck_reminders_sent"),
        CheckConstraint("last_error_code IS NULL OR last_error_code IN ('CONNECT_FAILED','RATE_LIMITED','PROVIDER_REJECTED','DELIVERY_UNCERTAIN','CHAT_UNAVAILABLE','TELEGRAM_NOT_CONNECTED','INVALID_STATE','RETRY_EXHAUSTED','DELIVERY_EXPIRED','CONFIGURATION_FAILED')", name="ck_reminders_error"),
        Index("ix_reminders_pending", "delivery_state", "next_attempt_at"),
        Index("ix_reminders_lease", "delivery_state", "lease_until"),
    )
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(32))


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


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skills"),
        UniqueConstraint("normalized_key", name="uq_skills_normalized_key"),
        CheckConstraint("length(canonical_name) BETWEEN 1 AND 100 AND length(trim(canonical_name)) > 0", name="ck_skills_name_length"),
        CheckConstraint("length(normalized_key) BETWEEN 1 AND 512 AND length(trim(normalized_key)) > 0", name="ck_skills_key_length"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512).with_variant(String(512, collation="C"), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SkillAlias(Base):
    __tablename__ = "skill_aliases"
    __table_args__ = (
        PrimaryKeyConstraint("normalized_alias", name="pk_skill_aliases"),
        CheckConstraint("length(alias) BETWEEN 1 AND 100 AND length(trim(alias)) > 0", name="ck_skill_aliases_name_length"),
        CheckConstraint("length(normalized_alias) BETWEEN 1 AND 512 AND length(trim(normalized_alias)) > 0", name="ck_skill_aliases_key_length"),
    )
    normalized_alias: Mapped[str] = mapped_column(String(512).with_variant(String(512, collation="C"), "postgresql"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE", name="fk_skill_aliases_skill"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (
        PrimaryKeyConstraint("job_id", "skill_id", "requirement_kind", name="pk_job_skills"),
        CheckConstraint("requirement_kind IN ('required','preferred')", name="ck_job_skills_requirement_kind"),
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE", name="fk_job_skills_job"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT", name="fk_job_skills_skill"), primary_key=True)
    requirement_kind: Mapped[str] = mapped_column(String(16), primary_key=True)


class UserSkill(Base):
    __tablename__ = "user_skills"
    __table_args__ = (PrimaryKeyConstraint("user_profile_id", "skill_id", name="pk_user_skills"),)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE", name="fk_user_skills_profile"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT", name="fk_user_skills_skill"), primary_key=True)


class DiscoverFeedChain(Base):
    __tablename__ = "discover_feed_chains"
    __table_args__ = (
        CheckConstraint("state_count BETWEEN 1 AND 64 AND page_count BETWEEN 0 AND 32", name="ck_feed_chain_capacity"),
        CheckConstraint("expires_at > created_at", name="ck_feed_chain_expiry"),
        Index("ix_feed_chain_expiry", "expires_at"),
        Index("ix_feed_chain_creator_expiry", "creator_id", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Quota attribution only. Cursors are shareable, not creator-authorized.
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    identity_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_count: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    page_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")


class DiscoverFeedState(Base):
    __tablename__ = "discover_feed_states"
    __table_args__ = (
        CheckConstraint("status IN ('ready','claimed','complete')", name="ck_feed_state_status"),
        CheckConstraint("(status = 'claimed' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (status <> 'claimed' AND lease_token IS NULL AND lease_until IS NULL)", name="ck_feed_state_lease"),
        CheckConstraint("(status = 'complete' AND result_state IS NOT NULL AND result_state IN ('page','paused','end')) OR (status <> 'complete' AND result_state IS NULL)", name="ck_feed_state_result"),
        CheckConstraint("length(payload) <= 262144", name="ck_feed_state_payload"),
        Index("ix_feed_state_chain", "chain_id"),
        Index("ix_feed_state_prev", "prev_token"),
        Index("ix_feed_state_next", "next_token"),
    )
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    chain_id: Mapped[str] = mapped_column(ForeignKey("discover_feed_chains.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    result_state: Mapped[str | None] = mapped_column(String(16))
    prev_token: Mapped[str | None] = mapped_column(ForeignKey("discover_feed_states.token", ondelete="SET NULL"))
    next_token: Mapped[str | None] = mapped_column(ForeignKey("discover_feed_states.token", ondelete="SET NULL"))
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscoverInventoryKey(Base):
    __tablename__ = "discover_inventory_keys"
    __table_args__ = (
        UniqueConstraint("identity_digest", name="uq_inventory_key_digest"),
        CheckConstraint("length(identity_digest) = 64", name="ck_inventory_key_digest"),
        CheckConstraint("length(canonical_identity) > 0", name="ck_inventory_key_identity"),
        CheckConstraint("updated_at >= created_at", name="ck_inventory_key_time"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    identity_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_identity: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiscoverInventoryGeneration(Base):
    __tablename__ = "discover_inventory_generations"
    __table_args__ = (
        CheckConstraint("status IN ('building','ready','failed','expired')", name="ck_inventory_generation_status"),
        CheckConstraint("source_page_size = 100 AND next_source_page = pages_committed AND pages_committed BETWEEN 0 AND 101", name="ck_inventory_generation_pages"),
        CheckConstraint("records_examined BETWEEN 0 AND 10000 AND item_count BETWEEN 0 AND 1000 AND item_count <= records_examined AND payload_bytes BETWEEN 0 AND 16777216", name="ck_inventory_generation_capacity"),
        CheckConstraint("source_total IS NULL OR (source_total BETWEEN 0 AND 10000 AND records_examined <= source_total)", name="ck_inventory_generation_total"),
        CheckConstraint("page_attempts >= 0 AND total_retry_count >= 0", name="ck_inventory_generation_attempts"),
        CheckConstraint("(lease_token IS NULL AND lease_until IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND status = 'building')", name="ck_inventory_generation_lease"),
        CheckConstraint("status <> 'building' OR (completed_at IS NULL AND failed_at IS NULL AND fresh_until IS NULL AND failure_code IS NULL)", name="ck_inventory_generation_building"),
        CheckConstraint("status <> 'ready' OR (source_exhausted AND source_total IS NOT NULL AND completed_at IS NOT NULL AND fresh_until IS NOT NULL AND fresh_until > completed_at AND failed_at IS NULL AND failure_code IS NULL AND lease_token IS NULL)", name="ck_inventory_generation_ready"),
        CheckConstraint("status <> 'failed' OR (failed_at IS NOT NULL AND failure_code IS NOT NULL AND length(trim(failure_code)) > 0 AND completed_at IS NULL AND fresh_until IS NULL AND lease_token IS NULL)", name="ck_inventory_generation_failed"),
        CheckConstraint("(started_at IS NULL OR started_at >= created_at) AND (completed_at IS NULL OR completed_at >= created_at) AND (failed_at IS NULL OR failed_at >= created_at)", name="ck_inventory_generation_time"),
        Index("uq_inventory_building_key", "key_id", unique=True,
              postgresql_where=text("status = 'building'"), sqlite_where=text("status = 'building'")),
        Index("ix_inventory_generation_fresh", "key_id", "status", "fresh_until"),
        Index("ix_inventory_generation_due", "status", "next_attempt_at"),
        Index("ix_inventory_generation_lease", "status", "lease_until"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_id: Mapped[str] = mapped_column(ForeignKey("discover_inventory_keys.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="building", server_default="building")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    source_page_size: Mapped[int] = mapped_column(nullable=False, default=100, server_default="100")
    next_source_page: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    source_total: Mapped[int | None] = mapped_column()
    source_exhausted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    pages_committed: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    records_examined: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    item_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    payload_bytes: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    last_successful_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_worked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    total_retry_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source_error: Mapped[str | None] = mapped_column(String(64))


class DiscoverInventoryItem(Base):
    __tablename__ = "discover_inventory_items"
    __table_args__ = (
        UniqueConstraint("generation_id", "source", "source_scope", "external_id", name="uq_inventory_item_identity"),
        CheckConstraint("ordinal >= 0", name="ck_inventory_item_ordinal"),
        CheckConstraint("length(trim(source)) > 0 AND length(trim(source_scope)) > 0 AND length(trim(external_id)) > 0", name="ck_inventory_item_identity"),
    )
    generation_id: Mapped[str] = mapped_column(ForeignKey("discover_inventory_generations.id", ondelete="RESTRICT"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    remote_evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiscoverInventoryView(Base):
    __tablename__ = "discover_inventory_views"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_inventory_view_digest"),
        CheckConstraint("length(token_digest) = 64", name="ck_inventory_view_digest"),
        CheckConstraint("page_size BETWEEN 1 AND 10", name="ck_inventory_view_page_size"),
        CheckConstraint("expires_at > created_at", name="ck_inventory_view_expiry"),
        Index("ix_inventory_view_generation_expiry", "generation_id", "expires_at"),
        Index("ix_inventory_view_expiry", "expires_at"),
        Index("ix_inventory_view_creator_expiry", "creator_user_id", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_id: Mapped[str] = mapped_column(ForeignKey("discover_inventory_generations.id", ondelete="RESTRICT"), nullable=False)
    page_size: Mapped[int] = mapped_column(nullable=False, default=10, server_default="10")
    creator_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
