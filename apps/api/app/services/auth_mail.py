"""Small delivery boundary. Tokens are never written to application logs."""
import json
import logging
import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from app.services.auth import AuthError


class CaptureMail:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise RuntimeError("AUTH_MAIL_SINK_DIR must be private (0700)")

    def check(self):
        if not os.access(self.directory, os.W_OK):
            raise AuthError("AUTH_UNAVAILABLE", 503)

    def send(self, purpose, recipient, link):
        path = self.directory / (secrets.token_hex(16) + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump({"purpose": purpose, "to": recipient, "link": link}, output)


class SMTPMail:
    def __init__(self):
        self.host = os.getenv("AUTH_SMTP_HOST", "")
        try:
            self.port = int(os.getenv("AUTH_SMTP_PORT", "465"))
            if not 1 <= self.port <= 65535: raise ValueError()
        except ValueError:
            raise RuntimeError("Configure valid AUTH_SMTP_PORT") from None
        self.sender = os.getenv("AUTH_MAIL_FROM", "")
        self.username = os.getenv("AUTH_SMTP_USERNAME", "")
        self.password = os.getenv("AUTH_SMTP_PASSWORD", "")
        if not self.host or not self.sender or any(c in self.sender for c in "\r\n"):
            raise RuntimeError("Configure AUTH_SMTP_HOST and AUTH_MAIL_FROM")

    def connection(self):
        server = smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=ssl.create_default_context())
        if self.username:
            try:
                server.login(self.username, self.password)
            except Exception:
                server.close()
                raise
        return server

    def check(self):
        try:
            with self.connection() as server:
                server.noop()
        except Exception:
            raise AuthError("AUTH_UNAVAILABLE", 503) from None

    def send(self, purpose, recipient, link):
        message = EmailMessage()
        message["From"], message["To"] = self.sender, recipient
        message["Subject"] = "Job Hunter: confirm email" if purpose == "verify" else "Job Hunter: reset password"
        message.set_content(f"Open this link only if you requested it:\n{link}\nIf not, ignore this message.")
        with self.connection() as server:
            server.send_message(message)


@lru_cache
def get_mailer():
    environment = os.getenv("APP_ENV", "production")
    mode = os.getenv("AUTH_MAIL_DELIVERY", "")
    if mode == "capture" and environment in {"development", "test"}:
        directory = os.getenv("AUTH_MAIL_SINK_DIR", "")
        if not directory or not Path(directory).is_absolute():
            raise RuntimeError("Configure absolute AUTH_MAIL_SINK_DIR for local capture")
        return CaptureMail(directory)
    if mode == "smtp":
        return SMTPMail()
    raise RuntimeError("Configure AUTH_MAIL_DELIVERY=smtp; capture is development/test only")


def web_origin():
    value = os.getenv("WEB_PUBLIC_ORIGIN", "")
    try:
        parts = urlsplit(value)
        parts.port  # Validate malformed/out-of-range ports at startup.
    except ValueError:
        raise RuntimeError("Configure trusted WEB_PUBLIC_ORIGIN") from None
    if (not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment
            or parts.scheme not in {"http", "https"}
            or (os.getenv("APP_ENV", "production") == "production" and parts.scheme != "https")
            or (parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost"})):
        raise RuntimeError("Configure trusted WEB_PUBLIC_ORIGIN (HTTPS in production)")
    return value


def deliver(purpose, recipient, raw):
    # Identity/token are already committed. Delivery failure never rolls them back,
    # nor changes the generic public result based on whether a recipient exists.
    page = "verify-email" if purpose == "verify" else "reset-password"
    try:
        get_mailer().send(purpose, recipient, f"{web_origin()}/{page}#token={raw}")
    except Exception:
        logging.getLogger(__name__).warning("auth_email_delivery_failed")
        # No provider exception, recipient or bearer link in logs/responses.
