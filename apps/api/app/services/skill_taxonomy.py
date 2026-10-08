"""Internal, transaction-owned taxonomy. No commits or outer rollbacks."""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Skill, SkillAlias
from app.services.skill_normalization import normalize_skill, prepare_skill_snapshot

BOOTSTRAP_VERSION = 1
BOOTSTRAP = (
    ("API", ()), ("Git", ()), ("HTML", ()), ("JavaScript", ("JS",)),
    ("Node.js", ("NodeJS",)), ("PostgreSQL", ("Postgres",)), ("Python", ()),
    ("React", ("ReactJS", "React.js")), ("Redis", ()),
    ("REST API", ("REST", "REST-API")), ("SQL", ()),
)


class SkillAliasConflict(Exception):
    def __init__(self):
        super().__init__("skill_alias_conflict")


def _flush_before_savepoint(session: Session) -> None:
    session.flush()
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        # sqlite3 legacy transaction mode does not BEGIN for SELECT. A root
        # SAVEPOINT would otherwise commit on RELEASE and escape outer rollback.
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")


def _lookup(session: Session, key: str) -> Skill | None:
    return session.scalar(select(Skill).join(SkillAlias, SkillAlias.skill_id == Skill.id)
                          .where(SkillAlias.normalized_alias == key))


def _unique_race(error: IntegrityError, table: str, column: str, constraint: str) -> bool:
    """Recognize only the expected insert race, not arbitrary integrity failures."""
    original = error.orig
    if getattr(original, "sqlstate", None) == "23505":
        return getattr(getattr(original, "diag", None), "constraint_name", None) == constraint
    return (getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
            or getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_PRIMARYKEY") and str(original) == f"UNIQUE constraint failed: {table}.{column}"


def _create_identity(session: Session, key: str, display: str) -> tuple[Skill, bool]:
    existing = _lookup(session, key)
    if existing is not None:
        return existing, False
    # Explicitly separate owner flush failures from a nested dictionary race.
    _flush_before_savepoint(session)
    try:
        with session.begin_nested():
            candidate = Skill(canonical_name=display, normalized_key=key)
            session.add(candidate)
            session.flush()
            session.add(SkillAlias(normalized_alias=key, skill_id=candidate.id, alias=display))
            session.flush()
        return candidate, True
    except IntegrityError as error:
        if not (_unique_race(error, "skills", "normalized_key", "uq_skills_normalized_key")
                or _unique_race(error, "skill_aliases", "normalized_alias", "pk_skill_aliases")):
            raise
        winner = _lookup(session, key)
        if winner is None:
            raise SkillAliasConflict() from None
        return winner, False


def register_alias(session: Session, skill: Skill, alias: str) -> None:
    key, display = normalize_skill(alias)
    owner = _lookup(session, key)
    if owner is not None:
        if owner.id != skill.id:
            raise SkillAliasConflict()
        return
    _flush_before_savepoint(session)
    try:
        with session.begin_nested():
            session.add(SkillAlias(normalized_alias=key, skill_id=skill.id, alias=display))
            session.flush()
    except IntegrityError as error:
        if not _unique_race(error, "skill_aliases", "normalized_alias", "pk_skill_aliases"):
            raise
        winner = _lookup(session, key)
        if winner is None or winner.id != skill.id:
            raise SkillAliasConflict() from None


def bootstrap_skills(session: Session) -> None:
    """Versioned, all-or-nothing seed; repeat safely within every live sync."""
    _flush_before_savepoint(session)
    with session.begin_nested():
        for display, aliases in BOOTSTRAP:
            key, _ = normalize_skill(display)
            skill, _ = _create_identity(session, key, display)
            if skill.normalized_key != key:
                raise SkillAliasConflict()
            for alias in sorted(aliases, key=lambda value: normalize_skill(value)[0]):
                register_alias(session, skill, alias)


@dataclass
class ResolvedSkills:
    skill_ids: set[int]
    ids_by_key: dict[str, int] = field(default_factory=dict)
    created_count: int = 0
    existing_count: int = 0
    invalid_count: int = 0
    quarantined: bool = False


def resolve_skills(session: Session, values: object) -> ResolvedSkills:
    snapshot = prepare_skill_snapshot(values)
    result = ResolvedSkills(set(), invalid_count=snapshot.invalid_count, quarantined=snapshot.quarantined)
    if snapshot.quarantined:
        return result
    bootstrap_skills(session)
    for key, display in sorted(snapshot.labels.items()):
        skill, created = _create_identity(session, key, display)
        result.skill_ids.add(skill.id)
        result.ids_by_key[key] = skill.id
        result.created_count += int(created)
        result.existing_count += int(not created)
    return result
