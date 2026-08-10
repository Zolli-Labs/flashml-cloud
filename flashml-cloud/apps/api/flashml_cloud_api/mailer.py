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
click. A bare ``httpx.AsyncClient(timeout=10.0)`` applies that 10s to each
of connect/read/write/pool independently, which is worst-case ~30s inside
that click, not 10 — so this caps connect at 5s and everything else at 10s.
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
        # httpx.Timeout(10.0) alone applies 10s to connect/read/write/pool
        # independently — worst case ~30s inside an admin's click. Capping
        # connect separately at 5s is what actually makes "waits 10" true.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))

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
        except Exception:
            # Deliberately broader than httpx.HTTPError: httpx.InvalidURL,
            # httpx.CookieConflict and httpx.StreamError all sit outside
            # HTTPError's hierarchy and would otherwise escape this handler.
            # This is also deliberately broader than the CoordinatorClient
            # precedent at app.py:382, which catches only httpx.HTTPError
            # and re-raises as a 502 — there, surfacing the failure is
            # right, because nothing has been committed yet. Here the
            # opposite holds: the admission has ALREADY been committed to
            # the database by the time this runs, and the whole point of
            # this module is that a mail failure must never reach the
            # caller and undo it. `except Exception` still lets
            # `asyncio.CancelledError` (a BaseException, not an Exception)
            # propagate, which is correct — cancellation should not be
            # swallowed.
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
