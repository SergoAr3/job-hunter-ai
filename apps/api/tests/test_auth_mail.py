"""Explicit delivery configuration and sanitized transport failures."""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from app.services import auth_mail, auth


@pytest.mark.parametrize("origin",["", "http://public.example.com", "https://host/path", "https://user:pass@host", "https://host?x=1", "https://host#x", "https://host:invalid", "https://host:99999", "https://host/", "http://127.0.0.1:3100"])
def test_production_origin_rejects_unsafe_values(monkeypatch,origin):
    monkeypatch.setenv("APP_ENV","production");monkeypatch.setenv("WEB_PUBLIC_ORIGIN",origin)
    with pytest.raises(RuntimeError): auth_mail.web_origin()


def test_trusted_origin_not_derived_from_headers(monkeypatch):
    monkeypatch.setenv("APP_ENV","production");monkeypatch.setenv("WEB_PUBLIC_ORIGIN","https://jobs.example.com")
    assert auth_mail.web_origin()=="https://jobs.example.com"


@pytest.mark.parametrize("mode",["", "capture", "smtp"])
def test_production_missing_email_configuration_fails_clearly(monkeypatch,mode):
    auth_mail.get_mailer.cache_clear()
    monkeypatch.setenv("APP_ENV","production");monkeypatch.setenv("AUTH_MAIL_DELIVERY",mode)
    monkeypatch.delenv("AUTH_SMTP_HOST",raising=False);monkeypatch.delenv("AUTH_MAIL_FROM",raising=False)
    try:
        with pytest.raises(RuntimeError): auth_mail.get_mailer()
    finally:auth_mail.get_mailer.cache_clear()


def test_capture_requires_private_absolute_directory(tmp_path,monkeypatch):
    auth_mail.get_mailer.cache_clear();monkeypatch.setenv("AUTH_MAIL_SINK_DIR","relative")
    try:
        with pytest.raises(RuntimeError): auth_mail.get_mailer()
    finally:auth_mail.get_mailer.cache_clear()
    directory=tmp_path/"mail";directory.mkdir(mode=0o755)
    with pytest.raises(RuntimeError): auth_mail.CaptureMail(directory)
    directory.chmod(0o700)
    capture=auth_mail.CaptureMail(directory);capture.send("verify","user@example.com","https://host/verify-email#token=private")
    file=next(directory.glob("*.json"));assert file.stat().st_mode & 0o077==0
    assert json.loads(file.read_text())["purpose"]=="verify"


def test_smtp_tls_delivery_and_no_secret_logging(monkeypatch,caplog):
    monkeypatch.setenv("AUTH_SMTP_HOST","smtp.example.com");monkeypatch.setenv("AUTH_MAIL_FROM","sender@example.com")
    monkeypatch.setenv("AUTH_SMTP_USERNAME","user");monkeypatch.setenv("AUTH_SMTP_PASSWORD","private-provider-secret")
    server=MagicMock();server.__enter__.return_value=server
    factory=MagicMock(return_value=server);monkeypatch.setattr(auth_mail.smtplib,"SMTP_SSL",factory)
    mailer=auth_mail.SMTPMail();mailer.check();mailer.send("reset","user@example.com","https://jobs.example.com/reset-password#token=private")
    assert factory.call_args.kwargs["context"].check_hostname
    assert factory.call_args.kwargs["timeout"]==10
    server.login.assert_called_with("user","private-provider-secret")
    message=server.send_message.call_args.args[0]
    assert message["To"]=="user@example.com" and "reset password" in message["Subject"]
    assert "reset-password#token=private" in message.get_content()
    factory.side_effect=RuntimeError("private-provider-secret")
    with pytest.raises(auth.AuthError) as result: mailer.check()
    assert result.value.code=="AUTH_UNAVAILABLE" and "private-provider-secret" not in caplog.text
