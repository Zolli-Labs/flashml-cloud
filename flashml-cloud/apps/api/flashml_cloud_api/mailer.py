"""Sends product mail through Resend's HTTP API.

Two emails exist today — access approved and access declined — and both are
transactional: a direct response to something the recipient themselves did.
That is why there is no unsubscribe here. The first marketing or digest mail
changes that and must not be bolted onto this path.

Inert unless both RESEND_API_KEY and EMAIL_FROM are set. That is not a
convenience: every other test file in this suite constructs Settings without
mail configured, and a deploy with no provider must still boot and serve.

Mirrors CoordinatorClient's shape deliberately — settings in, an optional
httpx transport for tests, one AsyncClient per call. It does not mirror its
timeout: 60s is right for a coordinator hop and wrong inside an admin's
click, so this waits 10.
"""
from __future__ import annotations

import json
import logging

import httpx

from .settings import Settings

log = logging.getLogger("flashml-cloud-api")

RESEND_ENDPOINT = "https://api.resend.com/emails"


class Mailer:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._api_key = settings.resend_api_key
        self._from = settings.email_from
        self._reply_to = settings.email_reply_to or settings.email_from
        self._transport = transport
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        """Both halves or nothing. A key with no From address cannot send."""
        return bool(self._api_key and self._from)

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        user_id: str = "",
    ) -> bool:
        """True if the provider accepted it. Never raises.

        The caller has already committed a database write it must not undo,
        so every failure here is reported as False and swallowed. Nothing
        about the recipient or the response is logged: the address is
        personal data and a provider error body can echo the request,
        which carries the API key.
        """
        if not self.configured:
            return False

        payload = {
            "from": self._from,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if self._reply_to:
            payload["reply_to"] = self._reply_to

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except httpx.HTTPError:
            log.error(
                json.dumps({"text": "email send failed", "reason": "transport",
                            "user_id": user_id})
            )
            return False

        if response.status_code >= 400:
            log.error(
                json.dumps({"text": "email send failed",
                            "status": response.status_code, "user_id": user_id})
            )
            return False
        return True
