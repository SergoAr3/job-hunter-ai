"""Finite, operator-owned rebuilding of derived rows; dry-run is read-only."""
from dataclasses import asdict, dataclass, field
from collections.abc import Callable

from sqlalchemy import inspect, or_, select, func
from sqlalchemy.orm import Session

from app.models import Job, JobSkill, Skill, SkillAlias, User, UserProfile, UserSkill
from app.services.skill_normalization import normalize_skill, prepare_skill_snapshot
from app.services.skill_sync import sync_job_skills, sync_profile_skills
from app.services.skill_taxonomy import BOOTSTRAP

FOUNDATION_TABLES = {"skills", "skill_aliases", "job_skills", "user_skills"}
_SEED_KEYS = {normalize_skill(alias)[0]: normalize_skill(label)[0]
              for label, aliases in BOOTSTRAP for alias in (label, *aliases)}


@dataclass
class ReconciliationReport:
    dry_run: bool
    schema_ready: bool
    owners_checked: int = 0
    owners_needing_sync: int = 0
    expected_associations: int = 0
    current_associations: int = 0
    added: int = 0
    removed: int = 0
    unknown_keys: int = 0
    invalid_values: int = 0
    quarantined_owners: int = 0
    canonical_alias_violations: int = 0
    last_processed_id: dict[str, int] = field(default_factory=dict)


def _inspect_owner(session: Session, owner: Job | UserProfile, ready: bool):
    profile = isinstance(owner, UserProfile)
    snapshots = [("profile", prepare_skill_snapshot(owner.skills))] if profile else [
        ("required", prepare_skill_snapshot(owner.required_skills)),
        ("preferred", prepare_skill_snapshot(owner.nice_to_have_skills)),
    ]
    if ready:
        current = {(value, "profile") for value in session.scalars(select(UserSkill.skill_id).where(UserSkill.user_profile_id == owner.id))} if profile else set(
            session.execute(select(JobSkill.skill_id, JobSkill.requirement_kind).where(JobSkill.job_id == owner.id)).tuples())
    else:
        current = set()
    invalid = sum(snapshot.invalid_count for _, snapshot in snapshots)
    if any(snapshot.quarantined for _, snapshot in snapshots):
        return current, current, set(), invalid, True
    keys = {key for _, snapshot in snapshots for key in snapshot.labels}
    lookup_keys = keys | {_SEED_KEYS[key] for key in keys if key in _SEED_KEYS}
    aliases = dict(session.execute(select(SkillAlias.normalized_alias, SkillAlias.skill_id).where(SkillAlias.normalized_alias.in_(lookup_keys))).tuples().all()) if ready and lookup_keys else {}
    desired = set()
    unknown = set()
    for kind, snapshot in snapshots:
        for key in snapshot.labels:
            canonical_key = _SEED_KEYS.get(key, key)
            identity = aliases.get(key, aliases.get(canonical_key))
            if identity is None:
                unknown.add(canonical_key)
                # Symbolic keys remain internal, never included in reports/logs.
                identity = canonical_key
            desired.add((identity, kind))
    return desired, current, unknown, invalid, False


def reconcile_skills(session_factory: Callable[[], Session], *, entity: str = "all",
                     dry_run: bool = True, batch_size: int = 100, after_id: int = 0,
                     limit: int = 1000) -> dict:
    if entity not in {"profiles", "jobs", "all"} or not 1 <= batch_size <= 1000 or limit < 1 or after_id < 0:
        raise ValueError("invalid_reconciliation_options")
    if entity == "all" and after_id:
        raise ValueError("resume_each_entity_separately")
    with session_factory() as session:
        ready = FOUNDATION_TABLES <= set(inspect(session.connection()).get_table_names())
        if not ready and not dry_run:
            raise ValueError("foundation_schema_missing")
        violations = session.scalar(select(func.count()).select_from(Skill).outerjoin(
            SkillAlias, SkillAlias.normalized_alias == Skill.normalized_key).where(
                or_(SkillAlias.skill_id.is_(None), SkillAlias.skill_id != Skill.id))) if ready else 0
    report = ReconciliationReport(dry_run, ready, canonical_alias_violations=violations or 0)
    unknown_keys = set()
    selected_count = 0
    scopes = ("profiles", "jobs") if entity == "all" else (entity,)
    for scope in scopes:
        model = UserProfile if scope == "profiles" else Job
        cursor = after_id
        while selected_count < limit:
            with session_factory() as session:
                ids = list(session.scalars(select(model.id).where(model.id > cursor).order_by(model.id)
                                          .limit(min(batch_size, limit - selected_count))))
            if not ids:
                break
            for owner_id in ids:
                selected_count += 1
                with session_factory() as session:
                    if not dry_run and scope == "profiles":
                        user_id = session.scalar(select(UserProfile.user_id).where(UserProfile.id == owner_id))
                        if user_id is not None:
                            session.scalar(select(User).where(User.id == user_id).with_for_update()
                                           .execution_options(populate_existing=True))
                    statement = select(model).where(model.id == owner_id)
                    if not dry_run:
                        statement = statement.with_for_update().execution_options(populate_existing=True)
                    owner = session.scalar(statement)
                    if owner is not None:
                        desired, current, unknown, invalid, quarantined = _inspect_owner(session, owner, ready)
                        report.owners_checked += 1
                        report.invalid_values += invalid
                        report.quarantined_owners += int(quarantined)
                        report.expected_associations += len(desired)
                        report.current_associations += len(current)
                        report.owners_needing_sync += int(desired != current)
                        unknown_keys.update(unknown)
                        if dry_run:
                            report.added += len(desired - current)
                            report.removed += len(current - desired)
                        elif not quarantined:
                            result = sync_profile_skills(session, owner) if scope == "profiles" else sync_job_skills(session, owner)
                            report.added += result.added
                            report.removed += result.removed
                            session.commit()
                    # Closing the read-only session rolls back; no dry-run writes.
                cursor = owner_id
                report.last_processed_id[scope] = cursor
    report.unknown_keys = len(unknown_keys)
    return asdict(report)
