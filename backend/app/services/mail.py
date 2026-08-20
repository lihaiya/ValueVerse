from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


class MailConfigurationError(RuntimeError):
    pass


def send_email_change_code(recipient: str, code: str) -> None:
    settings = get_settings()
    if not settings.smtp_configured:
        raise MailConfigurationError("system email is not configured")

    sender = settings.smtp_from_email.strip() or settings.smtp_username.strip()
    message = EmailMessage()
    message["Subject"] = "valueverse邮箱变更验证码"
    message["From"] = f"{settings.smtp_from_name} <{sender}>"
    message["To"] = recipient
    message.set_content(
        "您好，\n\n"
        f"您正在修改valueverse的绑定邮箱，验证码为：{code}\n"
        "验证码有效期为10分钟。若非本人操作，请忽略本邮件。\n\n"
        "valueverse"
    )

    tls_context = _smtp_tls_context()

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host.strip(),
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=tls_context,
        ) as smtp:
            smtp.login(settings.smtp_username.strip(), settings.smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(
        settings.smtp_host.strip(),
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls(context=tls_context)
        smtp.login(settings.smtp_username.strip(), settings.smtp_password)
        smtp.send_message(message)


def _smtp_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context
