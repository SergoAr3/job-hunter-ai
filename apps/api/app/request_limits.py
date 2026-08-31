from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.cv_profile_draft import ERROR_FILE_TOO_LARGE, MAX_UPLOAD_BYTES

CV_UPLOAD_MULTIPART_OVERHEAD_BYTES = 128 * 1024
MAX_CV_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + CV_UPLOAD_MULTIPART_OVERHEAD_BYTES


class _RequestBodyTooLarge(Exception):
    pass


class CVUploadBodyLimitMiddleware:
    """Bound CV multipart bodies before Starlette parses and spools file parts."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_CV_UPLOAD_REQUEST_BYTES,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_cv_upload_request(scope):
            await self.app(scope, receive, send)
            return

        if _declared_content_length_exceeds(scope, self.max_body_bytes):
            await _too_large_response(scope, receive, send)
            return

        received_bytes = 0
        response_started = False
        body_too_large = False

        async def limited_receive() -> Message:
            nonlocal body_too_large, received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    body_too_large = True
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if body_too_large:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            body_too_large = True
        if body_too_large:
            if response_started:
                raise RuntimeError("CV request body exceeded its limit after response start")
            await _too_large_response(scope, receive, send)


def _is_cv_upload_request(scope: Scope) -> bool:
    if scope["type"] != "http" or scope.get("method") != "POST":
        return False
    parts = scope.get("path", "").split("/")
    return (
        len(parts) == 5
        and parts[0] == ""
        and parts[1] == "users"
        and bool(parts[2])
        and parts[3:] == ["profile", "draft-from-cv"]
    )


def _declared_content_length_exceeds(scope: Scope, limit: int) -> bool:
    for name, raw_value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            if int(raw_value) > limit:
                return True
        except ValueError:
            continue
    return False


async def _too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=413,
        content={"detail": ERROR_FILE_TOO_LARGE},
    )
    await response(scope, receive, send)
