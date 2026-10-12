"""Read-only compatibility audit. Registry IDs never define matcher equivalence."""
from dataclasses import dataclass

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, aliased

from app.models import Skill, SkillAlias
from app.services.matcher_identity_policy import get_matcher_identity_policy


@dataclass(frozen=True)
class RegistryIssue:
    reason_code: str
    # Only a key from the public frozen policy, never a database-derived label.
    policy_alias: str | None = None


@dataclass(frozen=True)
class RegistryCompatibility:
    algorithm_version: str
    policy_version: str
    aliases_expected: int
    aliases_checked: int
    issues: tuple[RegistryIssue, ...] = ()

    @property
    def compatible(self) -> bool:
        return not self.issues


def validate_registry_compatibility(session: Session, *,
                                    algorithm_version: str = "job-match-v2.2") -> RegistryCompatibility:
    """Check the fixed policy namespace in the caller's transaction; never repair.

    This does not check per-owner association drift or unknown exact keys.
    Database execution failures propagate to the caller without logging here.
    """
    policy = get_matcher_identity_policy(algorithm_version)
    keys = tuple(sorted(policy.aliases))
    with session.no_autoflush:
        if not {"skills", "skill_aliases"} <= set(inspect(session.connection()).get_table_names()):
            return RegistryCompatibility(algorithm_version, policy.policy_version, len(keys), 0,
                                         (RegistryIssue("registry_schema_missing"),))
        canonical = aliased(SkillAlias)
        # v2.2 has exactly 12 keys: one bounded batch SELECT, no dictionary scan.
        statement = (select(SkillAlias.normalized_alias, SkillAlias.skill_id,
                            Skill.normalized_key, canonical.skill_id)
                     .outerjoin(Skill, Skill.id == SkillAlias.skill_id)
                     .outerjoin(canonical, canonical.normalized_alias == Skill.normalized_key)
                     .where(SkillAlias.normalized_alias.in_(keys)))
        rows = {key: (identity, canonical_key, canonical_owner)
                for key, identity, canonical_key, canonical_owner in session.execute(statement)}
    issues = []
    for key in keys:
        row = rows.get(key)
        if row is None:
            issues.append(RegistryIssue("policy_alias_missing", key))
            continue
        identity, canonical_key, canonical_owner = row
        if canonical_key != policy.aliases[key]:
            issues.append(RegistryIssue("policy_alias_wrong_target", key))
        if canonical_owner != identity:
            issues.append(RegistryIssue("canonical_alias_inconsistent", key))
    return RegistryCompatibility(algorithm_version, policy.policy_version, len(keys), len(keys), tuple(issues))
