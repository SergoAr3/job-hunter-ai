"""Derived associations; caller owns locks, commit and rollback."""
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Job, JobSkill, UserProfile, UserSkill
from app.services.skill_normalization import prepare_skill_snapshot
from app.services.skill_taxonomy import resolve_skills


@dataclass
class SkillSyncResult:
    added: int = 0
    removed: int = 0
    invalid_count: int = 0
    quarantined: bool = False


def sync_profile_skills(session: Session, profile: UserProfile) -> SkillSyncResult:
    session.flush()
    resolved = resolve_skills(session, profile.skills)
    result = SkillSyncResult(invalid_count=resolved.invalid_count, quarantined=resolved.quarantined)
    if resolved.quarantined:
        return result
    current = set(session.scalars(select(UserSkill.skill_id).where(UserSkill.user_profile_id == profile.id)))
    removed = current - resolved.skill_ids
    added = resolved.skill_ids - current
    if removed:
        session.execute(delete(UserSkill).where(UserSkill.user_profile_id == profile.id, UserSkill.skill_id.in_(removed)))
    session.add_all(UserSkill(user_profile_id=profile.id, skill_id=skill_id) for skill_id in sorted(added))
    session.flush()
    result.added, result.removed = len(added), len(removed)
    return result


def sync_job_skills(session: Session, job: Job) -> SkillSyncResult:
    session.flush()
    snapshots = [prepare_skill_snapshot(job.required_skills), prepare_skill_snapshot(job.nice_to_have_skills)]
    if any(item.quarantined for item in snapshots):
        return SkillSyncResult(invalid_count=sum(item.invalid_count for item in snapshots), quarantined=True)
    # Resolve the union in one stable key order, including across buckets.
    labels = {**snapshots[1].labels, **snapshots[0].labels}
    resolved = resolve_skills(session, list(labels.values()))
    desired = {(resolved.ids_by_key[key], kind)
               for snapshot, kind in zip(snapshots, ("required", "preferred"))
               for key in snapshot.labels}
    current = set(session.execute(select(JobSkill.skill_id, JobSkill.requirement_kind).where(JobSkill.job_id == job.id)).tuples())
    removed, added = current - desired, desired - current
    for skill_id, kind in sorted(removed):
        session.execute(delete(JobSkill).where(JobSkill.job_id == job.id, JobSkill.skill_id == skill_id, JobSkill.requirement_kind == kind))
    session.add_all(JobSkill(job_id=job.id, skill_id=skill_id, requirement_kind=kind) for skill_id, kind in sorted(added))
    session.flush()
    return SkillSyncResult(len(added), len(removed), sum(item.invalid_count for item in snapshots))
