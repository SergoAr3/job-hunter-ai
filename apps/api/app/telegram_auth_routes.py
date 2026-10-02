from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from app.database import get_session
from app.auth_dependencies import require_bot_service, require_principal, BOT_HEADER, DEV_HEADER
from app.services import auth, telegram_auth
from app.telegram_auth_schemas import ChallengeCreate, BrowserChallenge, BotChallenge, BotApproval

router = APIRouter(prefix="/auth/telegram", tags=["auth"])


def browser_principal(request: Request, session):
    if request.headers.getlist(BOT_HEADER) or request.headers.getlist(DEV_HEADER):
        raise auth.AuthError()
    return require_principal(request, session) if request.headers.getlist("authorization") else None


@router.post("/challenges")
def create(payload: ChallengeCreate, request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.create(session, payload.purpose, payload.binding.get_secret_value(), browser_principal(request, session), payload.password.get_secret_value() if payload.password else None)


@router.post("/status")
def status(payload: BrowserChallenge, request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.status(session, payload.token.get_secret_value(), payload.binding.get_secret_value(), payload.purpose, browser_principal(request, session))


@router.post("/complete")
def complete(payload: BrowserChallenge, request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.complete(session, payload.token.get_secret_value(), payload.binding.get_secret_value(), payload.purpose, browser_principal(request, session))


@router.post("/cancel")
def cancel_browser(payload: BrowserChallenge, request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.cancel_browser(session, payload.token.get_secret_value(), payload.binding.get_secret_value(), payload.purpose, browser_principal(request, session))


@router.post("/bot/inspect", dependencies=[Depends(require_bot_service)])
def inspect(payload: BotChallenge, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.inspect(session, payload.token.get_secret_value())


@router.post("/bot/approve", dependencies=[Depends(require_bot_service)])
def approve(payload: BotApproval, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.approve(session, payload.token.get_secret_value(), payload.telegram)


@router.post("/bot/cancel", dependencies=[Depends(require_bot_service)])
def cancel(payload: BotApproval, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return telegram_auth.cancel(session, payload.token.get_secret_value(), payload.telegram)
