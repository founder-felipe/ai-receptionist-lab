"""Twilio SMS adapter — stub for v1 demo.

The full v1 demo runs in MOCK mode (``SMS_ADAPTER=mock``). This module
exists so the factory in ``adapters/__init__.py`` can resolve
``SMS_ADAPTER=twilio`` once Twilio creds + the package are wired up.

The constructor raises ``NotImplementedError`` unless an explicit opt-in
gate is satisfied:

* ``LIVE_TWILIO=1`` set in the environment
* ``twilio`` package importable
* ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``, ``TWILIO_FROM_NUMBER`` present

Even when the gate opens this code path is a stub — exercised only in
future live testing.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from adapters.base import SMSAdapter


_GATED_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")


class TwilioSMS(SMSAdapter):
    """Send SMS via Twilio's REST API. Gated stub until v2."""

    name: ClassVar[str] = "twilio"  # type: ignore[misc]

    def __init__(self) -> None:
        if os.environ.get("LIVE_TWILIO") != "1":
            raise NotImplementedError(
                "Twilio SMS not yet implemented — "
                "set SMS_ADAPTER=mock for v1 demo"
            )

        try:
            from twilio.rest import Client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NotImplementedError(
                "Twilio SMS not yet implemented — install `twilio` package"
            ) from exc

        missing = [name for name in _GATED_ENV if not os.environ.get(name)]
        if missing:
            raise NotImplementedError(
                f"Twilio SMS not yet implemented — missing env: {', '.join(missing)}"
            )

        self._client = Client(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
        self._from_number: str = os.environ["TWILIO_FROM_NUMBER"]

    async def send(self, to: str, body: str) -> dict[str, Any]:
        # The Twilio SDK is sync. In real use we'd dispatch via
        # ``anyio.to_thread.run_sync``; this code path is exercised only
        # once the LIVE_TWILIO gate opens, so it stays minimal here.
        message = self._client.messages.create(
            to=to,
            from_=self._from_number,
            body=body,
        )
        return {
            "message_sid": message.sid,
            "status": "sent",
            "to": to,
            "body": body,
        }
