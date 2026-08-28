import logging
from dataclasses import asdict

from app.services.job_normalizer import NormalizedJobData, normalize_job
from app.services.job_posting_extractor import JobPostingExtractor
from app.services.safe_http_fetcher import FetchError, SafeHttpFetcher

logger = logging.getLogger(__name__)


class VacancyEnrichmentService:
    def __init__(self, fetcher: SafeHttpFetcher | None = None, extractor: JobPostingExtractor | None = None) -> None:
        self.fetcher = fetcher or SafeHttpFetcher()
        self.extractor = extractor or JobPostingExtractor()

    def enrich(self, url: str) -> tuple[NormalizedJobData | None, str | None]:
        try:
            page = self.fetcher.fetch(url)
            return normalize_job(self.extractor.extract(page.content)), None
        except FetchError as error:
            return None, error.code
        except Exception:
            logger.exception("Unexpected vacancy enrichment error")
            return None, "processing_failed"

    @staticmethod
    def values(data: NormalizedJobData) -> dict[str, object]: return asdict(data)
