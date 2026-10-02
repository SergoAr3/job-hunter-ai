from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth_dependencies import bearer_token, require_principal
from app.auth_schemas import AuthCredentials, CurrentUserOut, InternalPrincipalOut, LoginOut, RegisterIn
from app.database import get_session
from app.services.auth import Principal, current_user, login, logout, register


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=202)
def register_account(payload: RegisterIn, response: Response, session: Session = Depends(get_session)):
    register(session, payload)
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Registration request processed. You may sign in with your credentials."}


@router.post("/login", response_model=LoginOut)
def login_account(payload: AuthCredentials, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return login(session, payload)


@router.post("/logout", status_code=204)
def logout_account(request: Request, session: Session = Depends(get_session)):
    logout(session, bearer_token(request))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/me", response_model=CurrentUserOut)
def read_current_user(response: Response, principal: Principal = Depends(require_principal), session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return current_user(session, principal.user_id)


@router.get("/internal/principal", response_model=InternalPrincipalOut)
def read_internal_principal(response: Response, principal: Principal = Depends(require_principal), session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return InternalPrincipalOut(user_id=principal.user_id, me=current_user(session, principal.user_id))
