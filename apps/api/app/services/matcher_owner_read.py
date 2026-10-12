"""Read-only frozen mappings and owner consistency in the caller's transaction.

PostgreSQL order: Profile -> Job -> registry aliases/Skills (sorted alias key).
Profile PUT/CV Apply lock User -> Profile; Job enrichment locks Job. We never
acquire User or Application after these locks. Owner locks serialize semantic
refresh with relation sync. Registry SHARE locks keep checked mappings stable.
All successful locks live until the caller ends its transaction.
"""
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, aliased

from app.models import Job, JobSkill, Skill, SkillAlias, UserProfile, UserSkill
from app.services.matcher_hybrid_skills import HybridOccurrence, HybridSkills
from app.services.matcher_identity_policy import get_matcher_identity_policy, normalize_matcher_skill
from app.services.matcher_registry_compatibility import validate_registry_compatibility
from app.services.skill_identity_reader import FOUNDATION_TABLES
from app.services.skill_normalization import InvalidSkillValue


class HybridUnavailable(Exception):
    """Only bounded reason codes cross the evaluation boundary."""


def _semantic(values: object) -> list[tuple[str, str, str]]:
    if not isinstance(values, list):
        raise HybridUnavailable("invalid_semantic_input")
    policy = get_matcher_identity_policy("job-match-v2.2")
    try:
        return [(label, key, policy.aliases.get(key, key))
                for label in values for key in (normalize_matcher_skill(label),)]
    except InvalidSkillValue:
        raise HybridUnavailable("invalid_semantic_input") from None


def read_hybrid_skills(session: Session, profile: UserProfile, job: Job) -> HybridSkills:
    """Caller suppresses autoflush and supplies a connection-level savepoint.

Refreshing is safe only with a clean unit of work (checked by the dispatcher).
No bootstrap, resolver, sync, Session flush/commit/rollback or isolation change.
"""
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise HybridUnavailable("db_consistency_failure")
    if not FOUNDATION_TABLES <= set(inspect(session.connection()).get_table_names()):
        raise HybridUnavailable("schema_unavailable")

    for owner, model in ((profile, UserProfile), (job, Job)):
        refreshed = session.scalar(select(model).where(model.id == owner.id)
                                   .with_for_update(read=True)
                                   .execution_options(populate_existing=True))
        if refreshed is None:
            raise HybridUnavailable("owner_relation_drift")

    user = _semantic(profile.skills)
    required = _semantic(job.required_skills)
    preferred = _semantic(job.nice_to_have_skills)
    policy = get_matcher_identity_policy("job-match-v2.2")
    keys = set(policy.aliases)
    for _, key, identity in (*user, *required, *preferred):
        keys.update((key, identity))

    canonical = aliased(SkillAlias)
    mappings = {}
    ordered = sorted(keys)
    # Lock both non-null tables. Canonical aliases are included among the primary
    # keys for every safe mapping, so the nullable join need not be locked.
    for offset in range(0, len(ordered), 500):
        statement = (select(SkillAlias.normalized_alias, SkillAlias.skill_id,
                            Skill.normalized_key, canonical.skill_id)
                     .join(Skill, Skill.id == SkillAlias.skill_id)
                     .outerjoin(canonical, canonical.normalized_alias == Skill.normalized_key)
                     .where(SkillAlias.normalized_alias.in_(ordered[offset:offset + 500]))
                     .order_by(SkillAlias.normalized_alias)
                     .with_for_update(read=True, of=(SkillAlias, Skill)))
        for key, skill_id, target, canonical_owner in session.execute(statement):
            mappings[key] = (skill_id, target, canonical_owner)

    # A missing frozen key cannot become safe via a concurrent insertion that
    # was not locked by the mapping read.
    if not set(policy.aliases) <= mappings.keys() or not validate_registry_compatibility(session).compatible:
        raise HybridUnavailable("registry_incompatible")

    def resolve(entries: list[tuple[str, str, str]], bucket: str) -> tuple[HybridOccurrence, ...]:
        result = []
        for position, (label, key, identity) in enumerate(entries):
            raw, target = mappings.get(key), mappings.get(identity)
            if raw is None or target is None:
                raise HybridUnavailable("unresolved_identity")
            skill_id, canonical_key, canonical_owner = raw
            if canonical_key != identity or canonical_owner != skill_id or target != raw:
                # Unknown exact aliases may never expand frozen equivalence.
                raise HybridUnavailable("registry_incompatible")
            result.append(HybridOccurrence(bucket, position, label, identity, skill_id))
        return tuple(result)

    user_items = resolve(user, "profile")
    required_items = resolve(required, "required")
    preferred_items = resolve(preferred, "preferred")
    expected_user = {item.skill_id for item in user_items}
    actual_user = set(session.scalars(select(UserSkill.skill_id).where(UserSkill.user_profile_id == profile.id)))
    expected_job = {(item.skill_id, item.bucket) for item in (*required_items, *preferred_items)}
    actual_job = set(session.execute(select(JobSkill.skill_id, JobSkill.requirement_kind)
                                    .where(JobSkill.job_id == job.id)).tuples())
    if expected_user != actual_user or expected_job != actual_job:
        raise HybridUnavailable("owner_relation_drift")
    return HybridSkills(frozenset((item.policy_identity, item.skill_id) for item in user_items),
                        required_items, preferred_items)
