from urllib.parse import urlsplit

from app.models import JobSource


def detect_job_source(source_url: str) -> str:
    hostname = urlsplit(source_url).hostname
    if hostname is None:
        raise ValueError("source_url must include a host")
    hostname = hostname.lower()
    if _matches(hostname, "linkedin.com"):
        return JobSource.LINKEDIN.value
    if _matches(hostname, "hh.ru"):
        return JobSource.HH.value
    if hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return JobSource.GREENHOUSE.value
    if hostname == "jobs.lever.co":
        return JobSource.LEVER.value
    return JobSource.COMPANY_SITE.value


def _matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")
