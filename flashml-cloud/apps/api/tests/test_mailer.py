"""The mailer: inert without configuration, and silent about secrets.

The two assertions that matter are that an unconfigured deploy sends
nothing and raises nothing (every other test file in this suite runs that
way), and that a provider failure is reported as False rather than raised —
an admission must never fail because mail did.
"""
from __future__ import annotations

import json

import httpx
import pytest

from flashml_cloud_api.mailer import RESEND_ENDPOINT, Mailer
from flashml_cloud_api.settings import Settings


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_service_key="unused",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
    )
    base.update(over)
    return Settings(**base)


class FakeResend(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, boom: bool = False):
        self.requests: list[httpx.Request] = []
        self._status = status
        self._boom = boom

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self._boom:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(self._status, json={"id": "msg_123"})


def _configured(transport: FakeResend) -> Mailer:
    return Mailer(
        _settings(resend_api_key="re_test_key", email_from="FlashML <no-reply@mail.example>"),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_unconfigured_mailer_sends_nothing_and_raises_nothing():
    transport = FakeResend()
    mailer = Mailer(_settings(), transport=transport)
    assert mailer.configured is False
    assert await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t") is False
    assert transport.requests == []


@pytest.mark.asyncio
async def test_a_key_without_a_from_address_is_still_unconfigured():
    transport = FakeResend()
    mailer = Mailer(_settings(resend_api_key="re_test_key"), transport=transport)
    assert mailer.configured is False
    assert await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t") is False
    assert transport.requests == []


@pytest.mark.asyncio
async def test_configured_mailer_posts_to_resend_and_returns_true():
    transport = FakeResend()
    ok = await _configured(transport).send(
        to="her@example.com", subject="You're in", html="<p>hi</p>", text="hi"
    )
    assert ok is True
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert str(req.url) == RESEND_ENDPOINT
    assert req.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(req.content)
    assert body["to"] == ["her@example.com"]
    assert body["subject"] == "You're in"
    assert body["html"] == "<p>hi</p>"
    assert body["text"] == "hi"
    assert body["from"] == "FlashML <no-reply@mail.example>"


@pytest.mark.asyncio
async def test_reply_to_defaults_to_the_from_address():
    transport = FakeResend()
    await _configured(transport).send(to="a@b.c", subject="s", html="<p>h</p>", text="t")
    body = json.loads(transport.requests[0].content)
    assert body["reply_to"] == "FlashML <no-reply@mail.example>"


@pytest.mark.asyncio
async def test_explicit_reply_to_wins():
    transport = FakeResend()
    mailer = Mailer(
        _settings(
            resend_api_key="re_test_key",
            email_from="FlashML <no-reply@mail.example>",
            email_reply_to="humans@example.com",
        ),
        transport=transport,
    )
    await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t")
    body = json.loads(transport.requests[0].content)
    assert body["reply_to"] == "humans@example.com"


@pytest.mark.asyncio
async def test_provider_error_status_returns_false_and_does_not_raise():
    transport = FakeResend(status=500)
    assert await _configured(transport).send(
        to="a@b.c", subject="s", html="<p>h</p>", text="t"
    ) is False


@pytest.mark.asyncio
async def test_transport_failure_returns_false_and_does_not_raise():
    transport = FakeResend(boom=True)
    assert await _configured(transport).send(
        to="a@b.c", subject="s", html="<p>h</p>", text="t"
    ) is False


@pytest.mark.asyncio
async def test_failure_logs_carry_no_address_and_no_key(caplog):
    transport = FakeResend(status=500)
    with caplog.at_level("ERROR"):
        await _configured(transport).send(
            to="secret-person@example.com", subject="s", html="<p>h</p>", text="t",
            user_id="u-42",
        )
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-person@example.com" not in logged
    assert "re_test_key" not in logged
    # The failure must still be diagnosable: user_id and status, nothing else.
    assert "u-42" in logged
    assert "500" in logged
