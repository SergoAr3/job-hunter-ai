"""Opt-in manual comparison helper; never imported by pytest."""

import json
from pathlib import Path

from app.services.job_ai_enrichment import JobAIEnrichmentService, VacancyAIInput


def main() -> None:
    fixture_path = Path(__file__).parents[1] / "tests" / "fixtures" / "ai_enrichment" / "cases.json"
    service = JobAIEnrichmentService()
    if not service.configured:
        raise SystemExit("Set OPENAI_API_KEY to run manual fixture comparisons")
    for case in json.loads(fixture_path.read_text()):
        result, error = service.enrich(VacancyAIInput.model_validate(case["input"]))
        print(json.dumps({"id": case["id"], "error": error, "result": result.model_dump(mode="json") if result else None, "expect": case["expect"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
