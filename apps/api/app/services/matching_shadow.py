"""Pure counterfactual comparisons. Private labels never belong in reports."""
from dataclasses import dataclass, field

from app.models import Job, UserProfile
from app.schemas import MatchComponentOut
from app.services import job_matching as legacy
from app.services.skill_identity_reader import IdentityBatch, SemanticSkills, read_semantic_skills


BUCKETS = {"required": "required_skills", "preferred": "nice_to_have_skills"}
STATUSES = ("comparable", "evidence_unavailable", "drift", "unresolved", "invalid",
            "quarantined", "schema_missing")
PUBLIC_ALIAS_GROUPS = {
    "postgres": "postgresql", "postgresql": "postgresql",
    "js": "javascript", "javascript": "javascript",
    "rest": "rest", "rest api": "rest", "rest-api": "rest_api_dash",
    "nodejs": "node_js", "node.js": "node_js",
    "reactjs": "react", "react.js": "react", "react": "react",
}


@dataclass
class SkillView:
    component: MatchComponentOut
    denominator: int
    # Internal only: allows tests/audits to retain original occurrence positions.
    matched_positions: list[int] = field(default_factory=list)
    missing_positions: list[int] = field(default_factory=list)


@dataclass
class Evaluation:
    skills: dict[str, SkillView]
    score: int | None
    verdict: str
    coverage: int
    confidence: str | None
    recommendation: str
    primary_reason_code: str | None


@dataclass
class PairComparison:
    status: str
    reasons: list[str] = field(default_factory=list)
    views: dict[str, Evaluation] = field(default_factory=dict)
    consistency: dict[str, dict[str, int]] = field(default_factory=dict)
    duplicates: dict[str, dict[str, int]] = field(default_factory=dict)
    alias_groups: dict[str, int] = field(default_factory=dict)
    private_alias_matches: int = 0
    invalid_reasons: dict[str, int] = field(default_factory=dict)
    unresolved_keys: int = 0


def _evaluation(core: legacy._MatchCoreResult, skills: dict[str, SkillView]) -> Evaluation:
    reason = core.recommendation.primary_reason
    return Evaluation(skills, core.score, core.verdict, core.coverage, core.confidence,
                      core.recommendation.code, reason.code if reason else None)


def _counterfactual(profile: UserProfile, job: Job, base: legacy._MatchCoreResult,
                    skills: dict[str, SkillView], evidence: legacy.MatchEvidenceAvailability) -> Evaluation:
    components = dict(base.components)
    for kind, name in BUCKETS.items():
        components[name] = skills[kind].component
    # Exact v2.1 aggregation, including operation order and both rounding stages.
    # Non-skill components/conflicts come from the single baseline evaluation.
    evaluated_weight = sum(component.weight for component in components.values() if component.score is not None)
    coverage = evaluated_weight
    core_unavailable = components["role"].score is None and components["required_skills"].score is None
    if coverage < 40 or core_unavailable:
        score = None
        verdict = "insufficient_data"
        confidence = None
    else:
        weighted = sum(component.weight * component.score / 100 for component in components.values() if component.score is not None)
        score = legacy._round_to_five(weighted * 100 / evaluated_weight)
        verdict = "high" if score >= 75 else "medium" if score >= 50 else "low"
        confidence = legacy._confidence(coverage)
    _, gaps, unknowns = legacy._explanation_reasons(profile, job, components, evidence)
    recommendation = legacy._recommendation(verdict, base.conflicts, gaps, unknowns)
    reason = recommendation.primary_reason
    return Evaluation(skills, score, verdict, coverage, confidence, recommendation.code,
                      reason.code if reason else None)


def _identity_view(snapshot: SemanticSkills, aliases: dict[str, int], user_ids: set[int],
                   available: bool, name: str, direct_ids: set[int] | None = None) -> SkillView:
    entries = snapshot.occurrences
    if direct_ids is not None:
        # A clean pair has exactly these identities; retain one original label
        # for private internal reasons, without changing the semantic snapshot.
        first = {}
        for item in entries:
            identity = aliases[item.key]
            if identity in direct_ids:
                first.setdefault(identity, item)
        entries = list(first.values())
    denominator = len(snapshot.occurrences) if direct_ids is None else len(direct_ids)
    if not available:
        return SkillView(legacy._component(name, None, "unknown"), denominator)
    matched = [item for item in entries if aliases[item.key] in user_ids]
    missing = [item for item in entries if aliases[item.key] not in user_ids]
    score = legacy._round_percent(len(matched), denominator)
    status = "matched" if not missing else "partial" if matched else "mismatch"
    return SkillView(legacy._component(name, score, status,
                                      [item.label for item in matched], [item.label for item in missing]),
                     denominator, [item.position for item in matched], [item.position for item in missing])


def compare_pair(profile: UserProfile | None, job: Job | None, identities: IdentityBatch,
                 *, schema_ready: bool = True) -> PairComparison:
    if profile is None or job is None:
        return PairComparison("quarantined", ["owner_missing"])
    snapshots = {"profile": read_semantic_skills(profile.skills),
                 "required": read_semantic_skills(job.required_skills),
                 "preferred": read_semantic_skills(job.nice_to_have_skills)}
    result = PairComparison("comparable")
    for snapshot in snapshots.values():
        for code, count in snapshot.invalid_reasons.items():
            result.invalid_reasons[code] = result.invalid_reasons.get(code, 0) + count
    if any(snapshot.quarantined for snapshot in snapshots.values()):
        result.status = "quarantined"
        result.reasons = ["semantic_not_list"]
        return result
    if result.invalid_reasons:
        result.status = "invalid"
        result.reasons = ["semantic_item_invalid"]
        return result

    # Validate historical JSON shapes explicitly. Exceptions from the scorer
    # itself are internal failures, not ordinary dirty-data status codes.
    string_lists = (profile.target_roles, profile.location, job.language_requirements)
    if (any(not isinstance(values, list) or any(not isinstance(item, str) for item in values)
            for values in string_lists)
            or not isinstance(profile.languages, list)
            or any(not isinstance(item, dict) for item in profile.languages)):
        return PairComparison("invalid", ["legacy_input_invalid"])

    evidence = legacy.MatchEvidenceAvailability(skills=job.ai_enrichment_status == "success",
                                                languages=job.ai_enrichment_status == "success",
                                                location=job.workplace_type in {"onsite", "hybrid"})
    base = legacy._calculate_match_core(profile, job, evidence)
    legacy_skills = {kind: SkillView(base.components[name], len(snapshots[kind].occurrences))
                     for kind, name in BUCKETS.items()}
    result.views["legacy"] = _evaluation(base, legacy_skills)
    if not schema_ready:
        result.status = "schema_missing"
        result.reasons = ["foundation_tables_missing"]
        return result

    keys = set().union(*(snapshot.keys for snapshot in snapshots.values()))
    unresolved = keys - identities.aliases.keys()
    result.unresolved_keys = len(unresolved)
    if unresolved:
        result.reasons.append("identity_unresolved")
    expected = {kind: {identities.aliases[key] for key in snapshot.keys if key in identities.aliases}
                for kind, snapshot in snapshots.items()}
    job_sets = identities.jobs.get(job.id, {})
    actual = {"profile": identities.users.get(profile.id, set()),
              "required": job_sets.get("required", set()), "preferred": job_sets.get("preferred", set())}
    alias_drift = bool(keys & identities.inconsistent_keys)
    drift = alias_drift or bool(job_sets.keys() - BUCKETS.keys())
    if alias_drift:
        result.reasons.append("alias_inconsistent")
    if job_sets.keys() - BUCKETS.keys():
        result.reasons.append("association_bucket_invalid")
    for kind, desired in expected.items():
        current = actual[kind]
        result.consistency[kind] = {"expected": len(desired), "actual": len(current),
                                    "missing": len(desired - current), "extra": len(current - desired)}
        if desired != current:
            drift = True
    if drift:
        result.reasons.append("association_drift")
    if unresolved or drift:
        result.status = "unresolved" if unresolved else "drift"
        return result

    for kind, snapshot in snapshots.items():
        result.duplicates[kind] = {
            "same_key_collapse": len(snapshot.occurrences) - len(snapshot.keys),
            "alias_collapse": len(snapshot.keys) - len(expected[kind]),
        }
    result.duplicates["buckets"] = {"required_preferred_overlap": len(expected["required"] & expected["preferred"])}
    profile_keys_by_id = {}
    for key in snapshots["profile"].keys:
        profile_keys_by_id.setdefault(identities.aliases[key], set()).add(key)
    for kind in BUCKETS:
        for item in snapshots[kind].occurrences:
            equivalent_keys = profile_keys_by_id.get(identities.aliases[item.key], set()) - {item.key}
            if equivalent_keys:
                public_keys = equivalent_keys & PUBLIC_ALIAS_GROUPS.keys()
                groups = ({PUBLIC_ALIAS_GROUPS[key] for key in public_keys | {item.key}}
                          if item.key in PUBLIC_ALIAS_GROUPS and public_keys else set())
                if groups:
                    for group in sorted(groups):
                        result.alias_groups[group] = result.alias_groups.get(group, 0) + 1
                if item.key not in PUBLIC_ALIAS_GROUPS or equivalent_keys - PUBLIC_ALIAS_GROUPS.keys():
                    result.private_alias_matches += 1

    for mode in ("hybrid", "direct"):
        skills = {kind: _identity_view(snapshots[kind], identities.aliases, actual["profile"],
                                       base.components[name].score is not None, name,
                                       actual[kind] if mode == "direct" else None)
                  for kind, name in BUCKETS.items()}
        result.views[mode] = _counterfactual(profile, job, base, skills, evidence)
    if not evidence.skills or (base.components["required_skills"].score is None
                               and base.components["nice_to_have_skills"].score is None):
        result.status = "evidence_unavailable"
    return result
