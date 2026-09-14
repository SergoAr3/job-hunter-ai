"""Opt-in integration check against the migrated local PostgreSQL database."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User, UserProfile, WorkExperience
from app.services.work_experiences import save, WorkExperienceError
from app.work_experience_schema import WorkExperienceIn


@pytest.mark.skipif(os.getenv("RUN_WORK_HISTORY_POSTGRES") != "1", reason="requires migrated local PostgreSQL")
@pytest.mark.parametrize("near_limit", [False, True])
def test_concurrent_posts_serialize_duplicate_and_limit(near_limit):
    with SessionLocal() as session:
        user = User(telegram_id=-(uuid.uuid4().int % (2**62)), first_name="Work history integration test")
        session.add(user)
        session.flush()
        profile = UserProfile(user_id=user.id, target_roles=["Developer"])
        session.add(profile)
        session.commit()
        user_id, profile_id = user.id, profile.id
    try:
        if near_limit:
            with SessionLocal() as session:
                for index in range(19):
                    save(session, user_id, WorkExperienceIn(company=f"Initial {index}"))
        barrier = Barrier(2)
        def post(index):
            with SessionLocal() as session:
                barrier.wait(timeout=5)
                try:
                    save(session, user_id, WorkExperienceIn(company=f"New {index}" if near_limit else "Duplicate"))
                    return "created"
                except WorkExperienceError as error:
                    return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post, [0, 1]))
        assert sorted(results) == sorted(["created", "WORK_EXPERIENCE_LIMIT_REACHED" if near_limit else "DUPLICATE_WORK_EXPERIENCE"])
        with SessionLocal() as session:
            assert len(list(session.scalars(select(WorkExperience).where(WorkExperience.user_profile_id == profile_id)))) == (20 if near_limit else 1)
    finally:
        with SessionLocal() as session:
            session.delete(session.get(UserProfile, profile_id))
            session.flush()
            session.delete(session.get(User, user_id))
            session.commit()
