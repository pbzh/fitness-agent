"""Async email delivery using smtplib in a thread-pool executor.

SMTP config is loaded from SystemConfig(key="smtp") at send time, so changes
take effect immediately without restart.

Config schema (stored as JSONB, password Fernet-encrypted):
{
    "host": str,
    "port": int,          # typically 587 (STARTTLS) or 465 (SSL)
    "username": str,
    "password_enc": str,  # Fernet token
    "from_address": str,
    "use_tls": bool,      # STARTTLS (port 587)
    "use_ssl": bool,      # Implicit SSL (port 465)
}
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import partial

import structlog
from sqlmodel import select

from app.db.models import SystemConfig
from app.db.session import AsyncSessionLocal
from app.security.secrets import decrypt

log = structlog.get_logger()


async def _load_smtp_config() -> dict | None:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == "smtp")
            )
        ).scalar_one_or_none()
    return row.value if row else None


def _send_sync(cfg: dict, to: str, subject: str, body_html: str, body_text: str) -> None:
    """Blocking SMTP send — runs in executor."""
    password = decrypt(cfg["password_enc"]) if cfg.get("password_enc") else ""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_address"]
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    server: smtplib.SMTP
    if cfg.get("use_ssl"):
        server = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], int(cfg["port"]))

    with server:
        if cfg.get("use_tls") and not cfg.get("use_ssl"):
            server.starttls(context=ctx)
        if cfg.get("username"):
            server.login(cfg["username"], password or "")
        # Use authenticated username as envelope sender (MAIL FROM).
        # Providers like Gmail reject MAIL FROM != authenticated account.
        # msg["From"] header still shows the configured from_address to recipients.
        envelope_from = cfg.get("username") or cfg["from_address"]
        server.sendmail(envelope_from, [to], msg.as_string())


async def send_email(to: str, subject: str, body_html: str, body_text: str = "") -> bool:
    cfg = await _load_smtp_config()
    if not cfg or not cfg.get("host"):
        log.warning("SMTP not configured — skipping email", to=to, subject=subject)
        return False
    if not body_text:
        body_text = body_html
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, partial(_send_sync, cfg, to, subject, body_html, body_text)
        )
        log.info("Email sent", to=to, subject=subject)
        return True
    except Exception as exc:
        log.error("Email delivery failed", to=to, subject=subject, error=str(exc))
        return False


# ── Email templates ──────────────────────────────────────────────────────────

def _base_html(content: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:560px;margin:40px auto;color:#1a1a1a">
{content}
<hr style="margin-top:40px;border:none;border-top:1px solid #e5e5e5">
<p style="font-size:12px;color:#888">Coacher — automated notification</p>
</body></html>"""


async def send_registration_pending_email(to: str) -> bool:
    html = _base_html("""
<h2>Registration received</h2>
<p>Your Coacher account has been created and is waiting for admin approval.</p>
<p>You will receive another email as soon as your account is activated.</p>
""")
    text = (
        "Your Coacher account has been created and is waiting for admin approval. "
        "You will receive another email once activated."
    )
    return await send_email(to, "Your Coacher account is pending approval", html, text)


async def send_approval_email(to: str) -> bool:
    html = _base_html("""
<h2 style="color:#16a34a">Your account has been approved</h2>
<p>Your Coacher account has been approved. You can now log in.</p>
<p><a href="/" style="background:#16a34a;color:#fff;padding:10px 20px;
   border-radius:6px;text-decoration:none;display:inline-block">Log in</a></p>
""")
    text = "Your Coacher account has been approved. You can now log in."
    return await send_email(to, "Your Coacher account has been approved", html, text)


async def send_rejection_email(to: str) -> bool:
    html = _base_html("""
<h2 style="color:#dc2626">Account access revoked</h2>
<p>Your Coacher account approval has been revoked. Please contact the administrator.</p>
""")
    text = "Your Coacher account approval has been revoked. Please contact the administrator."
    return await send_email(to, "Coacher account access revoked", html, text)


async def send_registration_notification(admin_email: str, new_user_email: str) -> bool:
    html = _base_html(f"""
<h2>New user registration</h2>
<p><strong>{new_user_email}</strong> has registered and is waiting for approval.</p>
<p><a href="/#admin" style="background:#2563eb;color:#fff;padding:10px 20px;
   border-radius:6px;text-decoration:none;display:inline-block">Review in admin panel</a></p>
""")
    text = f"{new_user_email} registered and is waiting for approval."
    return await send_email(admin_email, f"New registration: {new_user_email}", html, text)
