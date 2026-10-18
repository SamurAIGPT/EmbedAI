import pytest
from fastapi import Request

from app.core.config import settings
from app.services.local_access_policy import (
    LocalAccessDenied,
    LocalAccessGrant,
    LoopbackPolicy,
)


def make_request(*, client=("127.0.0.1", 50000), headers=None) -> Request:
    headers = {"host": "localhost:8000", **(headers or {})}
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": client,
        }
    )


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


def test_loopback_request_gets_a_grant():
    assert isinstance(LoopbackPolicy().authorize(make_request()), LocalAccessGrant)


def test_disabled_feature_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    with pytest.raises(LocalAccessDenied, match="disabled"):
        LoopbackPolicy().authorize(make_request())


@pytest.mark.parametrize("client", [("203.0.113.9", 1), ("10.0.0.5", 1), None])
def test_non_loopback_client_is_refused(client):
    with pytest.raises(LocalAccessDenied, match="this machine"):
        LoopbackPolicy().authorize(make_request(client=client))


@pytest.mark.parametrize("client", [("127.0.0.1", 1), ("::1", 1), ("localhost", 1)])
def test_loopback_clients_are_accepted(client):
    LoopbackPolicy().authorize(make_request(client=client))


@pytest.mark.parametrize(
    "header", ["X-Forwarded-For", "Forwarded", "X-Forwarded-Host", "X-Real-IP"]
)
def test_proxied_request_is_refused_even_from_loopback(header):
    with pytest.raises(LocalAccessDenied, match="proxy"):
        LoopbackPolicy().authorize(make_request(headers={header: "198.51.100.7"}))


@pytest.mark.parametrize("host", ["evil.example", "abc.ngrok.app:443", "localhost.evil.example"])
def test_untrusted_host_is_refused(host):
    with pytest.raises(LocalAccessDenied, match="Host"):
        LoopbackPolicy().authorize(make_request(headers={"host": host}))


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_trusted_hosts_are_accepted(host):
    LoopbackPolicy().authorize(make_request(headers={"host": host}))


def test_missing_host_header_is_refused():
    request = make_request()
    request.scope["headers"] = []
    with pytest.raises(LocalAccessDenied, match="Host"):
        LoopbackPolicy().authorize(request)


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:3001"])
def test_untrusted_origin_is_refused(origin):
    with pytest.raises(LocalAccessDenied, match="Origin"):
        LoopbackPolicy().authorize(make_request(headers={"origin": origin}))


def test_frontend_origin_is_accepted():
    LoopbackPolicy().authorize(make_request(headers={"origin": "http://localhost:3000"}))


def test_grant_cannot_be_constructed_outside_the_policy():
    with pytest.raises(TypeError):
        LocalAccessGrant()
