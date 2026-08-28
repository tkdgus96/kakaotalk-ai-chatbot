"""Out-of-band alerts via email (SMTP).

KakaoTalk problems (logout / session / Iris down) mean the bot can't notify
through the chat itself, so alerts go over email instead. Also used for the
periodic OpenAI spend report.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import time
from email.message import EmailMessage

import httpx

from app.config import settings
from app.dependencies import logger


def smtp_configured() -> bool:
    return bool(
        settings.smtp_host
        and settings.smtp_user
        and settings.smtp_password
        and settings.alert_email_to
    )


def _send_email_sync(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.alert_email_from or settings.smtp_user
    msg["To"] = settings.alert_email_to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if settings.smtp_starttls:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ctx) as s:
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)


async def send_alert(subject: str, body: str) -> bool:
    if not smtp_configured():
        logger.warning("alert skipped (SMTP not configured): %s", subject)
        return False
    try:
        await asyncio.to_thread(_send_email_sync, f"[온반봇] {subject}", body)
        logger.info("alert email sent: %s", subject)
        return True
    except Exception as e:
        logger.warning("alert email failed (%s): %s", subject, e)
        return False


async def fetch_openai_spend_usd(days: int = 1) -> float | None:
    """Total OpenAI spend (USD) over the last `days` via the org Costs API.
    Requires an Admin key (OPENAI_ADMIN_KEY). Returns None if unavailable."""
    key = settings.openai_admin_key
    if not key:
        return None
    start = int(time.time()) - days * 86400
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                "https://api.openai.com/v1/organization/costs",
                params={"start_time": start, "bucket_width": "1d", "limit": days + 1},
                headers={"Authorization": f"Bearer {key}"},
            )
        if res.status_code != 200:
            logger.warning("costs api %s %.120s", res.status_code, res.text)
            return None
        total = 0.0
        for bucket in res.json().get("data", []):
            for result in bucket.get("results", []):
                amt = (result.get("amount") or {}).get("value")
                if amt is not None:
                    total += float(amt)
        return total
    except Exception as e:
        logger.warning("costs api failed: %s", e)
        return None


async def send_cost_report() -> bool:
    """Email the recent OpenAI spend. No official 'remaining balance' API exists,
    so we report spend (last 24h + last 7d)."""
    day = await fetch_openai_spend_usd(1)
    week = await fetch_openai_spend_usd(7)
    if day is None and week is None:
        logger.info("cost report skipped (no admin key / costs unavailable)")
        return False
    lines = ["OpenAI 사용액 리포트", ""]
    if day is not None:
        lines.append(f"- 최근 24시간: ${day:.2f}")
    if week is not None:
        lines.append(f"- 최근 7일: ${week:.2f}")
    lines.append("")
    lines.append("※ OpenAI는 '남은 잔액' API를 제공하지 않아 사용액만 표시합니다.")
    return await send_alert("일일 사용액 리포트", "\n".join(lines))
