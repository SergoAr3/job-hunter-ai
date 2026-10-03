from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.auth_dependencies import bearer_token, require_principal
from app.auth_schemas import AuthCredentials, CurrentUserOut, InternalPrincipalOut, LoginOut, RegisterIn
from app.services.auth_limits import limit_request
from app.services.auth_mail import get_mailer, web_origin
from app.services import email_tokens
from app.auth_schemas import EmailRequest, EmailTokenIn, ResetPasswordIn
from app.database import get_session
from app.services.auth import Principal, current_user, login, logout, register


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=202)
def register_account(payload: RegisterIn, request: Request, response: Response, session: Session = Depends(get_session)):
    limit_request(request, "register", payload.email)
    get_mailer().check()
    web_origin()
    register(session, payload)
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Request processed. Check your email if confirmation is required."}


@router.post("/login", response_model=LoginOut)
def login_account(payload: AuthCredentials, request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    limit_request(request, "login", payload.email)
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


@router.post("/email/resend", status_code=202)
def resend(payload: EmailRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    limit_request(request, "mail", payload.email)
    email_tokens.request_email(session, payload.email, "verify")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/password/forgot", status_code=202)
def forgot(payload: EmailRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    limit_request(request, "mail", payload.email)
    email_tokens.request_email(session, payload.email, "reset")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/email/verify")
def verify(payload: EmailTokenIn, request: Request, response: Response, session: Session = Depends(get_session)):
    limit_request(request, "consume")
    email_tokens.consume(session, payload.token.get_secret_value(), "verify")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/password/reset")
def reset(payload: ResetPasswordIn, request: Request, response: Response, session: Session = Depends(get_session)):
    limit_request(request, "consume")
    email_tokens.consume(session, payload.token.get_secret_value(), "reset", payload.password.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
