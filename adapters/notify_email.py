"""SMTP owner-notification adapter.

Sends a multipart text+HTML email to the venue owner when a lead lands.
Brand-styled HTML (warm-black background, amber-gold accent) keeps the
demo notification looking on-brand in the owner's inbox.

``smtplib`` is blocking, so the actual send is dispatched through
``anyio.to_thread.run_sync`` to keep the FastAPI event loop free.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar

import anyio.to_thread

from adapters.base import NotifyAdapter


_REQUIRED_SETTINGS = (
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASS",
    "SMTP_FROM",
    "SMTP_TO_OWNER",
)


def _build_html(lead: dict[str, Any], venue: dict[str, Any]) -> str:
    """Render a small brand-styled HTML email body."""
    rows = "".join(
        f"""
        <tr>
          <td style="padding:6px 14px;color:#c5b48a;
                     font-family:'DM Sans',Arial,sans-serif;font-size:13px;
                     text-transform:uppercase;letter-spacing:0.08em;">{key}</td>
          <td style="padding:6px 14px;color:#f4ecd8;
                     font-family:'DM Sans',Arial,sans-serif;font-size:15px;">
            {value}
          </td>
        </tr>
        """
        for key, value in lead.items()
        if value not in (None, "")
    )
    venue_name = venue.get("name", "")
    return f"""
    <html>
      <body style="margin:0;padding:0;background:#161310;">
        <table width="100%" cellpadding="0" cellspacing="0"
               style="background:#161310;padding:32px 0;">
          <tr><td align="center">
            <table width="540" cellpadding="0" cellspacing="0"
                   style="background:#1f1b16;border:1px solid #2c2620;
                          border-radius:8px;overflow:hidden;">
              <tr><td style="padding:24px 28px;border-bottom:1px solid #2c2620;">
                <div style="color:#d4a64a;
                            font-family:'Cormorant Garamond',Georgia,serif;
                            font-size:22px;letter-spacing:0.02em;">
                  New booking lead
                </div>
                <div style="color:#a89a7d;
                            font-family:'DM Sans',Arial,sans-serif;
                            font-size:13px;margin-top:4px;">
                  {venue_name}
                </div>
              </td></tr>
              <tr><td style="padding:18px 14px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  {rows}
                </table>
              </td></tr>
              <tr><td style="padding:18px 28px;border-top:1px solid #2c2620;
                              color:#7a6f5a;
                              font-family:'DM Sans',Arial,sans-serif;
                              font-size:12px;">
                Sent by The Rise AI Receptionist.
              </td></tr>
            </table>
          </td></tr>
        </table>
      </body>
    </html>
    """


def _build_text(lead: dict[str, Any], venue: dict[str, Any]) -> str:
    """Plain-text fallback body."""
    lines = [f"New booking lead — {venue.get('name', '')}", ""]
    for key, value in lead.items():
        if value in (None, ""):
            continue
        lines.append(f"{key}: {value}")
    lines += ["", "Sent by The Rise AI Receptionist."]
    return "\n".join(lines)


class EmailNotify(NotifyAdapter):
    """SMTP-over-SSL owner notifier."""

    name: ClassVar[str] = "email"  # type: ignore[misc]

    def __init__(self) -> None:
        from config import settings

        missing = [key for key in _REQUIRED_SETTINGS if not getattr(settings, key, "")]
        if missing:
            raise RuntimeError(
                f"EmailNotify missing required settings: {', '.join(missing)}"
            )

        self._host: str = settings.SMTP_HOST
        self._user: str = settings.SMTP_USER
        self._password: str = settings.SMTP_PASS
        self._from: str = settings.SMTP_FROM
        self._to: str = settings.SMTP_TO_OWNER

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self._host) as smtp:
            smtp.login(self._user, self._password)
            smtp.send_message(message)

    async def notify(
        self, lead: dict[str, Any], venue: dict[str, Any]
    ) -> dict[str, Any]:
        subject = (
            f"New booking lead: {lead.get('name', 'caller')} "
            f"— {lead.get('service', 'booking')}"
        )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from
        message["To"] = self._to
        message.set_content(_build_text(lead, venue))
        message.add_alternative(_build_html(lead, venue), subtype="html")

        await anyio.to_thread.run_sync(self._send_sync, message)

        return {
            "status": "delivered",
            "channel": "email",
            "to": self._to,
        }
