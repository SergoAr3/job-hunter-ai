"""Pure occurrence scoring after frozen identity and owner validation."""
from dataclasses import dataclass

from app.schemas import MatchComponentOut
from app.services.job_matching import _component, _round_percent


@dataclass(frozen=True)
class HybridOccurrence:
    bucket: str
    position: int
    label: str
    policy_identity: str
    skill_id: int


@dataclass(frozen=True)
class HybridSkills:
    # Membership uses policy identities as well as validated relational IDs.
    membership: frozenset[tuple[str, int]]
    required: tuple[HybridOccurrence, ...]
    preferred: tuple[HybridOccurrence, ...]


def evaluate_hybrid_skills(skills: HybridSkills, *, available: bool) -> dict[str, MatchComponentOut]:
    components = {}
    for name, occurrences in (("required_skills", skills.required),
                              ("nice_to_have_skills", skills.preferred)):
        if not available or not occurrences or not skills.membership:
            components[name] = _component(name, None, "unknown")
            continue
        matched, missing = [], []
        for item in occurrences:
            target = matched if (item.policy_identity, item.skill_id) in skills.membership else missing
            target.append(item.label)
        score = _round_percent(len(matched), len(occurrences))
        status = "matched" if not missing else "partial" if matched else "mismatch"
        components[name] = _component(name, score, status, matched, missing)
    return components
