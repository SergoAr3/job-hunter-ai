from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_session
from app.services import work_experiences as service
from app.work_experience_schema import WorkExperienceIn, WorkExperienceOut, WorkExperiencesOut

router = APIRouter(prefix="/users/{user_id}/profile/work-experiences")


@router.get("", response_model=WorkExperiencesOut)
def read(user_id: int, session: Session = Depends(get_session)):
    try:
        profile = service.profile_for_user(session, user_id)
        return {"items": [service.output(item) for item in service.list_for_profile(session, profile.id)]}
    except service.WorkExperienceError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


@router.post("", response_model=WorkExperienceOut, status_code=201)
def create(user_id: int, payload: WorkExperienceIn, session: Session = Depends(get_session)):
    try:
        return service.output(service.save(session, user_id, payload))
    except service.WorkExperienceError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


@router.put("/{experience_id}", response_model=WorkExperienceOut)
def update(user_id: int, experience_id: int, payload: WorkExperienceIn, session: Session = Depends(get_session)):
    try:
        return service.output(service.save(session, user_id, payload, experience_id))
    except service.WorkExperienceError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


@router.delete("/{experience_id}", status_code=204)
def delete(user_id: int, experience_id: int, session: Session = Depends(get_session)):
    try:
        service.delete(session, user_id, experience_id)
    except service.WorkExperienceError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None
