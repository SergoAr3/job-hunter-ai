"""Read existing identities only. No seed, resolution, flush or domain writes."""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import JobSkill, Skill, SkillAlias, UserSkill
from app.services.skill_normalization import InvalidSkillValue, normalize_skill
from app.services.skill_taxonomy import BOOTSTRAP


FOUNDATION_TABLES = {"skills", "skill_aliases", "user_skills", "job_skills"}
SEED_KEYS = {normalize_skill(value)[0]: normalize_skill(name)[0]
             for name, aliases in BOOTSTRAP for value in (name, *aliases)}


@dataclass(frozen=True)
class Occurrence:
    position: int
    label: str
    key: str


@dataclass
class SemanticSkills:
    occurrences: list[Occurrence] = field(default_factory=list)
    invalid_reasons: dict[str, int] = field(default_factory=dict)
    quarantined: bool = False

    @property
    def keys(self) -> set[str]:
        return {item.key for item in self.occurrences}


def read_semantic_skills(values: object) -> SemanticSkills:
    result = SemanticSkills(quarantined=not isinstance(values, list))
    if result.quarantined:
        return result
    for position, value in enumerate(values):
        try:
            key, _ = normalize_skill(value)
        except InvalidSkillValue as error:
            code = str(error)  # Fixed normalization reason code, never the value.
            result.invalid_reasons[code] = result.invalid_reasons.get(code, 0) + 1
        else:
            result.occurrences.append(Occurrence(position, value, key))
    return result


@dataclass
class IdentityBatch:
    aliases: dict[str, int] = field(default_factory=dict)
    inconsistent_keys: set[str] = field(default_factory=set)
    users: dict[int, set[int]] = field(default_factory=dict)
    jobs: dict[int, dict[str, set[int]]] = field(default_factory=dict)


def read_skill_identities(session: Session, keys: set[str], profile_ids: set[int],
                          job_ids: set[int]) -> IdentityBatch:
    result = IdentityBatch()
    canonical = aliased(SkillAlias)
    # Keep bound parameters below SQLite limits even for large semantic batches.
    ordered = sorted(keys)
    with session.no_autoflush:
        for offset in range(0, len(ordered), 500):
            statement = (select(SkillAlias.normalized_alias, SkillAlias.skill_id,
                                Skill.normalized_key, canonical.skill_id)
                         .join(Skill, Skill.id == SkillAlias.skill_id)
                         .outerjoin(canonical, canonical.normalized_alias == Skill.normalized_key)
                         .where(SkillAlias.normalized_alias.in_(ordered[offset:offset + 500])))
            for key, identity, canonical_key, canonical_owner in session.execute(statement):
                result.aliases[key] = identity
                if canonical_owner != identity or SEED_KEYS.get(key, canonical_key) != canonical_key:
                    result.inconsistent_keys.add(key)
        if profile_ids:
            for owner, identity in session.execute(select(UserSkill.user_profile_id, UserSkill.skill_id)
                                                   .where(UserSkill.user_profile_id.in_(profile_ids))):
                result.users.setdefault(owner, set()).add(identity)
        if job_ids:
            for owner, identity, kind in session.execute(select(JobSkill.job_id, JobSkill.skill_id,
                                                                JobSkill.requirement_kind)
                                                         .where(JobSkill.job_id.in_(job_ids))):
                result.jobs.setdefault(owner, {}).setdefault(kind, set()).add(identity)
    return result
