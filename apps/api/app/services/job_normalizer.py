from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExtractedJobData:
    title: str | None = None
    company: str | None = None
    description: str | None = None
    requirements_text: str | None = None
    salary_text: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    location: str | None = None
    workplace_raw: str | None = None
    employment_raw: str | None = None


@dataclass(frozen=True)
class NormalizedJobData:
    title: str | None
    company: str | None
    description: str | None
    requirements_text: str | None
    salary_text: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str
    salary_period_inferred: bool
    location: str | None
    workplace_type: str
    employment_type: str
    parsing_status: str


def normalize_job(data: ExtractedJobData) -> NormalizedJobData:
    cleaned = {name: _clean(name, getattr(data, name)) for name in ("title", "company", "description", "requirements_text", "salary_text", "location")}
    workplace = _workplace(data.workplace_raw)
    employment = _employment(data.employment_raw)
    period = _period(data.salary_period)
    salary_min, salary_max = _safe_salary_range(data.salary_min, data.salary_max)
    salary_present = bool(cleaned["salary_text"] or salary_min is not None or salary_max is not None)
    useful = any(cleaned.values()) or salary_present or workplace != "unknown" or employment != "unknown"
    complete = all((cleaned["title"], cleaned["company"], cleaned["description"], cleaned["requirements_text"], salary_present, cleaned["location"], workplace != "unknown", employment != "unknown"))
    return NormalizedJobData(**cleaned, salary_min=salary_min, salary_max=salary_max, salary_currency=_currency(data.salary_currency), salary_period=period, salary_period_inferred=False, workplace_type=workplace, employment_type=employment, parsing_status="success" if complete else "partial" if useful else "failed")


def _clean(name: str, value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    limit = {"title": 512, "company": 512, "location": 512}.get(name)
    return cleaned[:limit] if limit else cleaned


def _safe_salary_range(minimum: Decimal | None, maximum: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
        return None, None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None, None
    return minimum, maximum


def _period(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", " ")
    for period, words in {"hour": ("hour", "hourly", "hr"), "day": ("day", "daily"), "week": ("week", "weekly"), "month": ("month", "monthly"), "year": ("year", "yearly", "annual")}.items():
        if normalized in words:
            return period
    return "unknown"


def _currency(value: str | None) -> str | None:
    return value.upper() if value and len(value) == 3 and value.isalpha() else None


def _workplace(value: str | None) -> str:
    text = (value or "").lower()
    if "remote" in text or "telecommute" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if "on-site" in text or "onsite" in text or "in person" in text:
        return "onsite"
    return "unknown"


def _employment(value: str | None) -> str:
    text = (value or "").lower().replace("-", "_").replace(" ", "_")
    for kind, values in {"full_time": ("full_time", "fulltime"), "part_time": ("part_time", "parttime"), "contract": ("contract", "contractor"), "internship": ("internship", "intern"), "temporary": ("temporary", "temp")}.items():
        if text in values:
            return kind
    return "unknown"
