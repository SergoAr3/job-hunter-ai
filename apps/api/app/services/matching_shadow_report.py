"""Explicit privacy projection; never serialize comparison dataclasses wholesale."""
from collections import Counter

from app.services.job_matching import ALGORITHM_VERSION
from app.services.matching_shadow import BUCKETS, STATUSES, PairComparison


MODES = ("legacy", "hybrid", "direct")
TRANSITIONS = (("legacy", "hybrid"), ("hybrid", "direct"), ("legacy", "direct"))


def _delta(left: int | None, right: int | None) -> int | None:
    return right - left if left is not None and right is not None else None


def pair_diagnostic(application_id: int, user_id: int, profile_id: int | None,
                    job_id: int, comparison: PairComparison) -> dict:
    views = {}
    for mode, view in comparison.views.items():
        views[mode] = {
            "score": view.score, "verdict": view.verdict, "coverage": view.coverage,
            "confidence": view.confidence, "recommendation": view.recommendation,
            "primary_reason_code": view.primary_reason_code,
            "skills": {kind: {"matched": len(skill.component.matched),
                              "missing": len(skill.component.missing),
                              "denominator": skill.denominator, "score": skill.component.score,
                              "status": skill.component.status, "weight": skill.component.weight}
                       for kind, skill in view.skills.items()},
        }
    trusted = comparison.status == "comparable"
    deltas = {f"{left}_to_{right}": _delta(comparison.views[left].score, comparison.views[right].score)
              if trusted else None for left, right in TRANSITIONS}
    return {"application_id": application_id, "user_id": user_id, "profile_id": profile_id,
            "job_id": job_id, "status": comparison.status, "reasons": comparison.reasons,
            "views": views, "score_deltas": deltas, "consistency": comparison.consistency,
            "duplicates": comparison.duplicates, "alias_groups": comparison.alias_groups,
            "private_alias_matches": comparison.private_alias_matches,
            "invalid_reasons": comparison.invalid_reasons, "unresolved_keys": comparison.unresolved_keys}


class ShadowReport:
    def __init__(self, after_id: int, pair_limit: int):
        self.cursor = after_id
        self.pair_limit = pair_limit
        self.pairs = []
        self.statuses = Counter({status: 0 for status in STATUSES})
        self.skipped = 0
        self.schema_ready = True
        self.skills = {kind: {mode: {"evaluated_pairs": 0, "matched": 0, "denominator": 0}
                              for mode in MODES} for kind in BUCKETS}
        self.skill_deltas = {kind: {f"{left}_to_{right}": Counter() for left, right in TRANSITIONS}
                             for kind in BUCKETS}
        self.scores = {mode: Counter() for mode in MODES}
        self.score_deltas = {f"{left}_to_{right}": Counter() for left, right in TRANSITIONS}
        self.verdicts = {f"{left}_to_{right}": Counter() for left, right in TRANSITIONS}
        self.recommendations = {f"{left}_to_{right}": Counter() for left, right in TRANSITIONS}
        self.aliases = Counter()
        self.duplicates = Counter()

    def add(self, application_id: int, user_id: int, profile_id: int | None, job_id: int,
            comparison: PairComparison) -> None:
        self.statuses[comparison.status] += 1
        if len(self.pairs) < self.pair_limit:
            self.pairs.append(pair_diagnostic(application_id, user_id, profile_id, job_id, comparison))
        # No dirty or unavailable pair contributes to candidate distributions.
        if comparison.status != "comparable":
            return
        self.aliases.update(comparison.alias_groups)
        for kind, counts in comparison.duplicates.items():
            self.duplicates.update({f"{kind}.{name}": count for name, count in counts.items()})
        for mode, view in comparison.views.items():
            self.scores[mode][str(view.score) if view.score is not None else "null"] += 1
            for kind, skill in view.skills.items():
                if skill.component.score is not None:
                    totals = self.skills[kind][mode]
                    totals["evaluated_pairs"] += 1
                    totals["matched"] += len(skill.component.matched)
                    totals["denominator"] += skill.denominator
        for left, right in TRANSITIONS:
            a, b = comparison.views[left], comparison.views[right]
            name = f"{left}_to_{right}"
            delta = _delta(a.score, b.score)
            self.score_deltas[name][str(delta) if delta is not None else "null"] += 1
            self.verdicts[name][f"{a.verdict}->{b.verdict}"] += 1
            self.recommendations[name][f"{a.recommendation}->{b.recommendation}"] += 1
            for kind in BUCKETS:
                delta = _delta(a.skills[kind].component.score, b.skills[kind].component.score)
                self.skill_deltas[kind][name][str(delta) if delta is not None else "null"] += 1

    def output(self) -> dict:
        report = {
            "report_version": 1, "legacy_algorithm_version": ALGORITHM_VERSION,
            "schema_ready": self.schema_ready, "pairs_checked": sum(self.statuses.values()),
            "statuses": dict(self.statuses), "skipped_applications": self.skipped,
            "last_processed_id": self.cursor,
            "statistics_scope": "comparable_pairs_only",
            "skills": self.skills, "skill_score_delta_histograms": self.skill_deltas,
            "total_score_histograms": self.scores, "score_delta_histograms": self.score_deltas,
            "verdict_transitions": self.verdicts, "recommendation_transitions": self.recommendations,
            "alias_groups": dict(self.aliases), "duplicate_collapse_counts": dict(self.duplicates),
        }
        if self.pair_limit:
            report["pairs"] = self.pairs
            report["pair_diagnostics_truncated"] = sum(self.statuses.values()) > len(self.pairs)
        return report
