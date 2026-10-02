"""Authentication and path authorization, shared by all user-owned routes."""
import hmac
import ipaddress
import re

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings, get_auth_settings
from app.database import get_session
from app.services.auth import AuthError, Principal, authenticate


BOT_HEADER = "x-bot-service-token"
DEV_HEADER = "x-web-dev-api-token"


def bearer_token(request: Request) -> str:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1 or request.headers.getlist(BOT_HEADER):
        raise AuthError()
    match = re.fullmatch(r"(?i:Bearer) ([A-Za-z0-9_-]{43})", headers[0])
    if match is None:
        raise AuthError()
    return match.group(1)


def require_bot_service(request: Request, settings: AuthSettings = Depends(get_auth_settings)):
    headers = request.headers.getlist(BOT_HEADER)
    if (len(headers) != 1 or request.headers.getlist("authorization")
            or not hmac.compare_digest(headers[0].encode("utf-8"), settings.bot_service_token.encode("utf-8"))):
        raise AuthError()


def require_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    return authenticate(session, bearer_token(request))


def authorize_user(principal: Principal, user_id: int):
    if principal.user_id != user_id:
        raise AuthError("RESOURCE_NOT_FOUND", 404)


def require_user_access(
    request: Request, session: Session = Depends(get_session),
    settings: AuthSettings = Depends(get_auth_settings),
):
    # Inspect the matched route, not an untrusted browser user-id header.
    if "user_id" not in request.path_params:
        return
    if request.headers.getlist(BOT_HEADER):
        require_bot_service(request, settings)
        request.state.bot_target_user_id = request.path_params["user_id"]
        return
    if request.headers.getlist("authorization"):
        principal = require_principal(request, session)
        try:
            target = int(request.path_params["user_id"])
        except ValueError:
            raise AuthError("RESOURCE_NOT_FOUND", 404) from None
        authorize_user(principal, target)
        request.state.principal = principal
        return
    # Explicit server-only single-user credential. Host/XFF are not proof of locality.
    headers = request.headers.getlist(DEV_HEADER)
    if (settings.environment == "development" and settings.rollout_mode == "legacy-development"
            and settings.web_dev_api_token and len(headers) == 1 and request.client):
        try:
            local_client = ipaddress.ip_address(request.client.host).is_loopback
            target = int(request.path_params["user_id"])
        except ValueError:
            local_client, target = False, None
        if (local_client and target == settings.web_dev_user_id
                and hmac.compare_digest(headers[0].encode("utf-8"), settings.web_dev_api_token.encode("utf-8"))):
            return
    raise AuthError()
