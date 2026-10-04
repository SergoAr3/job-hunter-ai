from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.cv_import_schemas import ImportEdit
from app.models import User, UserProfile, WorkExperience, ProfileExperienceFact
from app.services.cv_import import apply_import, edit_import
from app.services.cv_import_state import get_import_store, CVImportError
from conftest import TestSessionLocal
from test_auth import plain, bearer, login, register
from test_cv_import import account, upload, action


def edit(account, preview, mutation, revision=None):
    uid, raw, _ = account
    return plain.patch(f"/users/{uid}/profile/cv-import/preview", headers=bearer(raw),
                       json={"token": preview["token"], "revision": preview["revision"] if revision is None else revision, "edit": mutation})


def test_edit_is_temporary_revision_guard_then_apply_exact_result(account):
    original = upload(account).json()
    changed = edit(account, original, {"kind": "work", "index": 0, "value": {**original["work_experience"][0], "company": "Edited project", "position": "Edited developer"}})
    assert changed.status_code == 200
    changed = changed.json()
    assert changed["revision"] == 2 and changed["expires_at"] == original["expires_at"]
    with TestSessionLocal() as db:
        assert db.query(UserProfile).count() == db.query(WorkExperience).count() == 0
    assert edit(account, original, {"kind": "delete_work", "index": 0}).json()["detail"]["code"] == "cv_import_revision_stale"
    assert action(account, original).json()["detail"]["code"] == "cv_import_revision_stale"
    changed = edit(account, changed, {"kind": "fact", "index": 0, "text": "  Corrected practical fact  "}).json()
    assert changed["experience_facts"] == ["Corrected practical fact"]
    assert action(account, changed, revision=changed["revision"]).status_code == 200
    with TestSessionLocal() as db:
        work = db.scalar(select(WorkExperience))
        assert (work.company, work.position) == ("Edited project", "Edited developer")
        assert db.scalar(select(ProfileExperienceFact.text)) == "Corrected practical fact"


def test_edit_delete_empty_cancel_keeps_domain(account):
    preview = upload(account).json()
    preview = edit(account, preview, {"kind": "delete_work", "index": 0}).json()
    preview = edit(account, preview, {"kind": "delete_fact", "index": 0}).json()
    assert preview["work_experience"] == preview["experience_facts"] == []
    assert action(account, preview, kind="cancel").status_code == 200
    assert edit(account, preview, {"kind": "delete_fact", "index": 0}).json()["detail"]["code"] == "cv_import_used"
    with TestSessionLocal() as db:
        assert db.query(UserProfile).count() == db.query(WorkExperience).count() == 0


def test_edit_profile_normalization_and_apply(account):
    preview = upload(account).json()
    value = {**preview["proposed"], "target_roles": ["  Developer  "], "skills": ["python", "Python"], "languages": [{"language": "Russian", "level": "native"}, {"language": " Русский ", "level": "native"}, {"language":" German ", "level":"b1"}]}
    preview = edit(account, preview, {"kind": "profile", "value": value}).json()
    assert preview["proposed"]["target_roles"] == ["Developer"]
    assert preview["proposed"]["skills"] == ["Python"]
    assert preview["proposed"]["languages"] == [{"language": "Russian", "level": "native"}, {"language":"German", "level":"B1"}]
    assert action(account, preview, revision=preview["revision"]).status_code == 200
    with TestSessionLocal() as db:
        profile = db.scalar(select(UserProfile))
        assert profile.languages == preview["proposed"]["languages"]
        assert profile.skills == preview["proposed"]["skills"]


@pytest.mark.parametrize("mutation", [
    {"kind": "work", "index": 0, "value": {"company": "X", "start_year": 2024, "end_year": 2020, "is_current": False}},
    {"kind": "work", "index": 0, "value": {"company": "X", "start_month": 2}},
    {"kind": "work", "index": 0, "value": {"company": "x" * 201}},
    {"kind": "fact", "index": 0, "text": " "},
    {"kind": "fact", "index": 0, "text": "x" * 501},
    {"kind": "fact", "index": 0, "text": "private\u0000marker"},
    {"kind": "delete_work", "index": 19},
])
def test_invalid_edit_safe_and_does_not_consume_or_change_preview(account, mutation):
    preview = upload(account).json()
    result = edit(account, preview, mutation)
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "cv_import_edit_invalid"
    assert "input" not in result.text and "ctx" not in result.text
    assert action(account, preview).status_code == 200


def test_edit_profile_limits_and_enums(account):
    preview = upload(account).json()
    for patch in [{"skills": [f"skill{i}" for i in range(31)]}, {"location": ["x" * 201]}, {"experience": "invented"}, {"languages": [{"language": "English", "level": "invented"}]}]:
        assert edit(account, preview, {"kind": "profile", "value": {**preview["proposed"], **patch}}).status_code == 422
    uid, raw, _ = account
    assert plain.patch(f"/users/{uid}/profile/cv-import/preview", headers={**bearer(raw), "Content-Type": "application/json"}, content='x' * (64 * 1024 + 1)).status_code == 413
    assert action(account, preview).status_code == 200


def test_edit_owner_session_expiry_and_terminal_checks(account):
    preview = upload(account).json()
    uid, raw, _ = account
    other = login()["session_token"]
    body = {"token": preview["token"], "revision": 1, "edit": {"kind": "delete_work", "index": 0}}
    assert plain.patch(f"/users/{uid}/profile/cv-import/preview", headers=bearer(other), json=body).status_code == 404
    register("other@example.com"); outsider = login("other@example.com")["session_token"]
    assert plain.patch(f"/users/{uid}/profile/cv-import/preview", headers=bearer(outsider), json=body).status_code in (403, 404)
    store = get_import_store()
    with closing(store._connect()) as c,c:
        c.execute("UPDATE previews SET expires=0 WHERE user_id=?", (uid,))
    assert edit(account, preview, {"kind": "delete_work", "index": 0}).status_code == 410


def test_parallel_same_revision_only_one_edit_wins(account):
    preview = upload(account).json()
    uid, raw, _ = account
    from app.services.auth import token_hash
    payload = ImportEdit(token=preview["token"], revision=1, edit={"kind": "fact", "index": 0, "text": "Edited"})
    def attempt(_):
        try:
            return edit_import(uid, token_hash(raw), payload, get_import_store()).revision
        except CVImportError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(attempt, range(2)))) == ["2", "cv_import_revision_stale"]


def test_existing_languages_and_lists_keep_merge_policy_after_edit(account):
    uid, _, _ = account
    with TestSessionLocal() as db:
        db.add(UserProfile(user_id=uid, target_roles=["Existing role"], skills=["Git"], languages=[{"language":"English", "level":"B1"}, {"language":"Russian", "level":"native"}]))
        db.commit()
    preview = upload(account).json()
    value = {**preview["proposed"], "target_roles":["New role"], "skills":["Docker"], "languages":[{"language":"Английский", "level":"A2"}, {"language":"Русский", "level":"native"}]}
    preview = edit(account, preview, {"kind":"profile", "value":value}).json()
    assert preview["proposed"]["target_roles"] == ["Existing role", "New role"]
    assert preview["proposed"]["skills"] == ["Git", "Docker"]
    assert preview["proposed"]["languages"] == [{"language":"English", "level":"B1"}, {"language":"Russian", "level":"native"}]
    assert action(account, preview, revision=preview["revision"]).status_code == 200


def test_edit_rejects_extra_payload_fields_and_used_preview(account):
    preview = upload(account).json()
    uid, raw, _ = account
    body = {"token":preview["token"], "revision":1, "edit":{"kind":"work", "index":0, "value":{"company":"Valid", "user_profile_id":999}}}
    assert plain.patch(f"/users/{uid}/profile/cv-import/preview", headers=bearer(raw), json=body).status_code == 422
    assert action(account, preview).status_code == 200
    assert edit(account, preview, {"kind":"delete_work", "index":0}).json()["detail"]["code"] == "cv_import_used"


@pytest.mark.parametrize("profile_state", ["missing", "empty", "existing"])
def test_edited_roles_locations_dedupe_and_preserve_existing_display(account, profile_state):
    if profile_state != "missing":
        with TestSessionLocal() as db:
            db.add(UserProfile(user_id=account[0],
                               target_roles=["DEVELOPER"] if profile_state == "existing" else [],
                               location=["YEREVAN"] if profile_state == "existing" else []))
            db.commit()
    preview = upload(account).json()
    value = {**preview["proposed"], "target_roles": ["Developer", " developer "],
             "location": ["Yerevan", " yerevan ", "Armenia", "Армения"]}
    response = edit(account, preview, {"kind": "profile", "value": value})
    assert response.status_code == 200
    changed = response.json()
    assert changed["revision"] == preview["revision"] + 1
    assert changed["proposed"]["target_roles"] == (["DEVELOPER"] if profile_state == "existing" else ["Developer"])
    assert changed["proposed"]["location"] == ["YEREVAN" if profile_state == "existing" else "Yerevan", "Armenia", "Армения"]
    assert action(account, changed, revision=changed["revision"]).status_code == 200
    with TestSessionLocal() as db:
        profile = db.scalar(select(UserProfile))
        assert profile.target_roles == changed["proposed"]["target_roles"]
        assert profile.location == changed["proposed"]["location"]


def test_edited_preview_rollback_safe(account, monkeypatch):
    preview = upload(account).json()
    assert action(account, preview).status_code == 200
    preview = upload(account).json()
    preview = edit(account, preview, {"kind": "work", "index": 0, "value": {"company": "Replacement"}}).json()
    uid, raw, _ = account
    from app.services.auth import token_hash
    with TestSessionLocal() as db:
        def fail(): raise SQLAlchemyError()
        monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(CVImportError):
            apply_import(db, uid, token_hash(raw), preview["token"], get_import_store(), preview["revision"])
    with TestSessionLocal() as db:
        assert db.scalar(select(WorkExperience.company)) == "Acme"
