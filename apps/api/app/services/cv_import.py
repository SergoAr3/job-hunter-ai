"""Backend-owned preview/Apply over the existing CV and profile contracts."""
import hashlib
import json

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.cv_import_schemas import ImportEdit, ImportPreview, ImportProfile
from app.models import ProfileExperienceFact, User, UserProfile, WorkExperience
from app.schemas import CVProfileDraftOut, LanguageIn, UserProfilePutIn, normalize_experience_fact_text
from app.services.cv_import_state import CVImportError, ImportStore
from app.services.profile_experience_facts import MAX_PROFILE_EXPERIENCE_FACTS, list_profile_experience_facts
from app.services.profile_normalization import profile_language_name_key
from app.services.work_experiences import MAX_WORK_EXPERIENCES, identity, list_for_profile
from app.work_experience_schema import WorkExperienceIn

PROFILE_FIELDS = tuple(UserProfilePutIn.model_fields)

# Import identity only: keep profile/CV display names and validation unchanged.
_LANGUAGE_ALIASES = {"русский": "russian", "армянский": "armenian", "английский": "english"}


def _merge_languages(existing, imported):
    result = []
    positions = {}
    for item in [*existing, *imported]:
        normalized = profile_language_name_key(item["language"])
        key = _LANGUAGE_ALIASES.get(normalized, normalized)
        if key not in positions:
            positions[key] = len(result)
            result.append(dict(item))
        else:
            retained = result[positions[key]]
            if not retained["level"].strip() and item["level"].strip():
                retained["level"] = item["level"]
    return result


def snapshot(session: Session, user_id: int, *, lock=False):
    # User lock also serializes imports when no profile exists yet.
    user = session.scalar(select(User).where(User.id == user_id).with_for_update()) if lock else session.get(User, user_id)
    if user is None:
        raise CVImportError("cv_import_invalid", 404)
    query = select(UserProfile).where(UserProfile.user_id == user_id)
    profile = session.scalar(query.with_for_update() if lock else query)
    current = ImportProfile.model_validate({key: getattr(profile, key) for key in PROFILE_FIELDS}).model_dump(mode="json") if profile else None
    work = list_for_profile(session, profile.id) if profile else []
    facts = list_profile_experience_facts(session, profile.id) if profile else []
    data = {"profile": current, "work": [list(identity(item)) for item in work], "facts": [item.text for item in facts]}
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return profile, current, work, facts, fingerprint


def _union(existing, imported, key=lambda value: " ".join(value.split()).casefold()):
    result = list(existing)
    seen = {key(item) for item in result}
    for item in imported:
        if key(item) not in seen:
            result.append(item)
            seen.add(key(item))
    return result


def prepare_preview(session: Session, user_id: int, session_hash: str, draft: CVProfileDraftOut, store: ImportStore):
    _, current, work, facts, fingerprint = snapshot(session, user_id)
    values = draft.model_dump(mode="json", exclude={"suggested_work_experience", "suggested_experience_facts"})
    for key in ("target_roles", "skills", "location"):
        values[key] = _union(current[key] if current else [], values[key])
    if current:
        if values["experience"] == "unknown":
            values["experience"] = current["experience"]
        if values["workplace_preference"] == "any":
            values["workplace_preference"] = current["workplace_preference"]
        if values["salary_min"] is None:
            for key in ("salary_min", "salary_currency", "salary_period"):
                values[key] = current[key]
    values["languages"] = _merge_languages(current["languages"] if current else [], values["languages"])
    # Imported languages are validated by the CV contract. Preserve readable
    # legacy existing levels rather than silently dropping or rewriting them.
    languages = values["languages"]
    try:
        if len(languages) > 30:
            raise ValueError()
        values = UserProfilePutIn.model_validate({**values, "languages": []}).model_dump(mode="json")
        values["languages"] = languages
    except (ValueError, ValidationError):
        raise CVImportError("cv_import_limit", 422) from None
    known = set()
    work_snapshot = []
    for entry in draft.suggested_work_experience:
        if identity(entry) not in known:
            work_snapshot.append(entry.model_dump(mode="json"))
            known.add(identity(entry))
    fact_snapshot = _union([], draft.suggested_experience_facts,
                           lambda text: normalize_experience_fact_text(text).casefold())
    if len(work_snapshot) > MAX_WORK_EXPERIENCES or len(fact_snapshot) > MAX_PROFILE_EXPERIENCE_FACTS:
        raise CVImportError("cv_import_limit", 422)
    payload = {"current": current, "proposed": values, "work_experience": work_snapshot,
               "work_experience_mode": "replace", "current_work_experience_count": len(work),
               "experience_facts": fact_snapshot, "experience_facts_mode": "replace",
               "current_experience_fact_count": len(facts), "fingerprint": fingerprint}
    token, expiry = store.issue(user_id, session_hash, payload)
    return ImportPreview(token=token, expires_at=expiry, **{key: value for key, value in payload.items() if key != "fingerprint"})


def edit_import(user_id: int, session_hash: str, edit: ImportEdit, store: ImportStore):
    def update(payload):
        if payload.get("work_experience_mode") != "replace" or payload.get("experience_facts_mode") != "replace":
            raise CVImportError("cv_import_stale")
        mutation = edit.edit
        if mutation.kind == "profile":
            values = mutation.value.model_dump(mode="json")
            current = payload["current"]
            # Retain the import merge policy; edits correct this import's proposal.
            for field in ("target_roles", "skills", "location"):
                values[field] = _union(current[field] if current else [], values[field])
            if current:
                if values["experience"] == "unknown":
                    values["experience"] = current["experience"]
                if values["workplace_preference"] == "any":
                    values["workplace_preference"] = current["workplace_preference"]
                if values["salary_min"] is None:
                    for field in ("salary_min", "salary_currency", "salary_period"):
                        values[field] = current[field]
            languages = values["languages"]
            legacy = {(x["language"], x["level"]) for x in current["languages"]} if current else set()
            languages = [item if (item["language"], item["level"]) in legacy
                         else LanguageIn.model_validate(item).model_dump() for item in languages]
            languages = _merge_languages(current["languages"] if current else [], languages)
            if len(languages) > 30:
                raise CVImportError("cv_import_limit", 422)
            values = UserProfilePutIn.model_validate({**values, "languages": []}).model_dump(mode="json")
            values["languages"] = languages
            payload["proposed"] = values
        else:
            collection = "work_experience" if mutation.kind in {"work", "delete_work"} else "experience_facts"
            if mutation.index >= len(payload[collection]):
                raise CVImportError("cv_import_edit_invalid", 422)
            if mutation.kind.startswith("delete_"):
                del payload[collection][mutation.index]
            elif mutation.kind == "work":
                payload[collection][mutation.index] = mutation.value.model_dump(mode="json")
            else:
                try:
                    text = normalize_experience_fact_text(mutation.text)
                except ValueError:
                    raise CVImportError("cv_import_edit_invalid", 422, {"text": "Проверьте текст: управляющие символы недопустимы."}) from None
                if not text:
                    raise CVImportError("cv_import_edit_invalid", 422, {"text": "Введите непустой текст."})
                payload[collection][mutation.index] = text
            # Same exact-identity dedupe as extraction; no fuzzy hiding.
            if collection == "work_experience":
                seen = set()
                unique = []
                for entry in payload[collection]:
                    key = identity(WorkExperienceIn.model_validate(entry))
                    if key not in seen:
                        seen.add(key)
                        unique.append(entry)
                payload[collection] = unique
            else:
                payload[collection] = _union([], payload[collection], lambda x: normalize_experience_fact_text(x).casefold())
        if len(payload["work_experience"]) > MAX_WORK_EXPERIENCES or len(payload["experience_facts"]) > MAX_PROFILE_EXPERIENCE_FACTS:
            raise CVImportError("cv_import_limit", 422)
        return payload
    payload, expires = store.edit(edit.token, user_id, session_hash, edit.revision, update)
    return ImportPreview(token=edit.token, expires_at=expires, **{k: v for k, v in payload.items() if k != "fingerprint"})


def apply_import(session: Session, user_id: int, session_hash: str, token: str, store: ImportStore, revision: int = 1):
    payload = store.claim(token, user_id, session_hash, revision) if revision != 1 else store.claim(token, user_id, session_hash)
    # Tokens stay terminal even after rollback/crash. A failed or uncertain
    # attempt requires a new preview, so replay can never duplicate work history.
    committing = False
    try:
        # Pre-change previews promised append, so they cannot authorize deletion.
        if payload.get("work_experience_mode") != "replace" or payload.get("experience_facts_mode") != "replace":
            raise CVImportError("cv_import_stale")
        profile, _, _, _, fingerprint = snapshot(session, user_id, lock=True)
        if fingerprint != payload["fingerprint"]:
            raise CVImportError("cv_import_stale")
        if profile is None:
            profile = UserProfile(user_id=user_id)
            session.add(profile)
        for key, value in ImportProfile.model_validate(payload["proposed"]).model_dump().items():
            setattr(profile, key, value)
        session.flush()
        session.execute(delete(WorkExperience).where(WorkExperience.user_profile_id == profile.id))
        # Reads order by descending ID; insert in reverse to preserve preview order.
        for entry in reversed(payload["work_experience"]):
            validated = WorkExperienceIn.model_validate(entry)
            session.add(WorkExperience(user_profile_id=profile.id, **validated.model_dump()))
        session.execute(delete(ProfileExperienceFact).where(ProfileExperienceFact.user_profile_id == profile.id))
        for text in payload["experience_facts"]:
            session.add(ProfileExperienceFact(user_profile_id=profile.id, text=text))
        session.flush()
        committing = True
        session.commit()
    except CVImportError:
        session.rollback()
        raise
    except (SQLAlchemyError, ValidationError):
        session.rollback()
        raise CVImportError("cv_import_unconfirmed" if committing else "cv_import_apply_failed", 503) from None
