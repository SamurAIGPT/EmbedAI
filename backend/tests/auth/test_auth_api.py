"""Owner sign-in, pairing, devices, CSRF and the Host/Origin/loopback rules,
through the real middleware and routes on the configured database."""

from collections.abc import AsyncGenerator
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import credentials, limiter, store
from app.auth.middleware import COOKIE_NAME, AuthMiddleware
from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models.auth import AuthSession, OwnerAccount, PairingCode
from app.db.models.link_code import LinkCode
from app.db.session import get_db
from app.routers.router import router as app_router

PASSPHRASE = "a long owner passphrase"
PUBLIC_HOST = "dots.example.com"
REMOTE = ("203.0.113.7", 40000)
LOCAL = ("127.0.0.1", 40000)


async def wipe(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        for model in (AuthSession, PairingCode, OwnerAccount):
            await db.execute(delete(model))
        await db.execute(delete(LinkCode).where(LinkCode.code.like("TST%")))
        await db.commit()


@pytest_asyncio.fixture
async def factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await wipe(sessions)
    try:
        yield sessions
    finally:
        await wipe(sessions)
        await engine.dispose()


@pytest.fixture(autouse=True)
def auth_settings(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_HOSTS", f"localhost,127.0.0.1,[::1],{PUBLIC_HOST}")
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "off")
    limiter.reset_all()
    yield
    limiter.reset_all()


@pytest.fixture
def app(factory) -> FastAPI:
    application = FastAPI()
    application.include_router(app_router)
    application.add_middleware(AuthMiddleware, session_factory=factory)

    async def database():
        async with factory() as db:
            yield db

    application.dependency_overrides[get_db] = database
    return application


def client(app, *, peer=REMOTE, base_url=f"http://{PUBLIC_HOST}", headers=None, behind=None) -> httpx.AsyncClient:
    """`behind` is the server's trusted-proxy list: the app is then wrapped in Uvicorn's own
    proxy-header middleware, as `python -m app.server` runs it. Without it the app sees the
    socket and headers only, like a server that has no proxy configured."""
    if behind is not None:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        app = ProxyHeadersMiddleware(app, trusted_hosts=",".join(behind))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=peer), base_url=base_url, headers=headers or {}
    )


async def set_owner(factory, passphrase=PASSPHRASE):
    async with factory() as db:
        await store.set_passphrase(db, passphrase, first_time_only=False)


async def login(api: httpx.AsyncClient, passphrase=PASSPHRASE) -> dict:
    response = await api.post("/api/v1/auth/login", json={"passphrase": passphrase})
    assert response.status_code == 200, response.text
    return response.json()


async def pairing_code(api: httpx.AsyncClient, csrf: str, scope="admin") -> str:
    response = await api.post("/api/v1/auth/pairing-codes", json={"scope": scope, "label": "Phone"},
                              headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    return response.json()["code"]


def error_code(response) -> str:
    return response.json()["detail"]["error_code"]


# --- Host, Origin and loopback -------------------------------------------------


async def test_health_is_public_and_everything_else_needs_a_session(app):
    async with client(app) as api:
        assert (await api.get("/api/v1/health")).status_code == 200
        response = await api.get("/api/v1/auth/session")
        assert response.status_code == 401 and error_code(response) == "auth_required"
        # A path that does not exist is refused the same way.
        assert (await api.get("/api/v1/no-such-route")).status_code == 401
        assert (await api.get("/docs")).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/auth/session"])
async def test_an_untrusted_host_header_is_refused_even_from_loopback(app, path):
    async with client(app, peer=LOCAL, base_url="http://localhost") as api:
        response = await api.get(path, headers={"Host": "rebind.attacker.example"})
        assert response.status_code == 421 and error_code(response) == "untrusted_host"


async def test_login_from_an_untrusted_host_is_refused(app, factory):
    await set_owner(factory)
    async with client(app, base_url="http://evil.example") as api:
        response = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})
        assert response.status_code == 421


async def test_an_untrusted_origin_is_refused(app):
    async with client(app) as api:
        response = await api.get("/api/v1/health", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403 and error_code(response) == "untrusted_origin"
        ok = await api.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
        assert ok.status_code == 200
        own = await api.get("/api/v1/health", headers={"Origin": f"http://{PUBLIC_HOST}"})
        assert own.status_code == 200


async def test_loopback_trust_is_off_by_default(app):
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        assert (await api.get("/api/v1/auth/session")).status_code == 401


async def test_loopback_programs_mode_trusts_programs_but_not_browsers(app, monkeypatch):
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "programs")
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        response = await api.get("/api/v1/auth/session")
        assert response.status_code == 200 and response.json()["kind"] == "loopback"
        browser = await api.get("/api/v1/auth/session", headers={"Sec-Fetch-Site": "same-site"})
        assert browser.status_code == 401
        page = await api.get("/api/v1/auth/session", headers={"Origin": "http://localhost:3000"})
        assert page.status_code == 401


async def test_loopback_all_mode_also_trusts_local_browsers(app, monkeypatch):
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "all")
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        browser = await api.get("/api/v1/auth/session", headers={"Sec-Fetch-Site": "same-site",
                                                                "Origin": "http://localhost:3000"})
        assert browser.status_code == 200


@pytest.mark.parametrize("header", [("X-Forwarded-For", "127.0.0.1"), ("Forwarded", "for=127.0.0.1"),
                                    ("X-Real-IP", "127.0.0.1"), ("X-Forwarded-Host", "localhost")])
async def test_proxy_headers_cancel_loopback_trust(app, monkeypatch, header):
    """A reverse proxy on this machine connects from loopback; what it forwards is remote."""
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "all")
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        response = await api.get("/api/v1/auth/session", headers={header[0]: header[1]})
        assert response.status_code == 401


async def test_a_remote_client_cannot_claim_loopback(app, monkeypatch):
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "all")
    async with client(app, peer=REMOTE, base_url="http://localhost:8000") as api:
        assert (await api.get("/api/v1/auth/session", headers={"X-Forwarded-For": "127.0.0.1"})).status_code == 401
        assert (await api.get("/api/v1/auth/session")).status_code == 401
    # Loopback socket, but the Host is the public name: that came through something else.
    async with client(app, peer=LOCAL) as api:
        assert (await api.get("/api/v1/auth/session")).status_code == 401


async def test_an_invalid_credential_never_falls_back_to_loopback(app, monkeypatch):
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "programs")
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        bad = await api.get("/api/v1/auth/session", headers={"Authorization": "Bearer ods_" + "x" * 43})
        assert bad.status_code == 401 and error_code(bad) == "session_invalid"


# --- owner setup and sign-in ---------------------------------------------------


async def test_first_run_setup_only_from_this_machine(app):
    async with client(app) as api:
        status = (await api.get("/api/v1/auth/status")).json()
        assert status == {"owner_configured": False, "setup_allowed": False, "desktop": False, "signed_in": False}
        remote = await api.post("/api/v1/auth/setup", json={"passphrase": PASSPHRASE})
        assert remote.status_code == 403 and error_code(remote) == "setup_local_only"
    async with client(app, peer=LOCAL, base_url="http://localhost:8000") as api:
        proxied = await api.post("/api/v1/auth/setup", json={"passphrase": PASSPHRASE},
                                 headers={"X-Forwarded-For": "198.51.100.1"})
        assert proxied.status_code == 403
        assert (await api.get("/api/v1/auth/status")).json()["setup_allowed"] is True
        weak = await api.post("/api/v1/auth/setup", json={"passphrase": "short"})
        assert weak.status_code == 422 and error_code(weak) == "weak_passphrase"
        form = await api.post("/api/v1/auth/setup", content=f"passphrase={PASSPHRASE}",
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert form.status_code == 415
        done = await api.post("/api/v1/auth/setup", json={"passphrase": PASSPHRASE},
                              headers={"Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-site"})
        assert done.status_code == 200, done.text
        assert done.json()["kind"] == "browser" and done.json()["csrf_token"]
        assert COOKIE_NAME in api.cookies
        again = await api.post("/api/v1/auth/setup", json={"passphrase": "another long passphrase"})
        assert again.status_code == 409


async def test_login_sets_an_httponly_samesite_cookie_and_session_works(app, factory):
    await set_owner(factory)
    async with client(app) as api:
        response = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert f"{COOKIE_NAME}=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
        assert "Secure" not in cookie  # plain http
        session = await api.get("/api/v1/auth/session")
        assert session.status_code == 200
        body = session.json()
        assert body["kind"] == "browser" and "admin" in body["scopes"]
        assert body["csrf_token"] == response.json()["csrf_token"]


async def test_cookie_is_secure_over_https_and_behind_a_trusted_tls_proxy(app, factory):
    from app.server import proxies

    await set_owner(factory)
    login_as = {"passphrase": PASSPHRASE}
    async with client(app, base_url=f"https://{PUBLIC_HOST}") as api:
        response = await api.post("/api/v1/auth/login", json=login_as)
        assert "Secure" in response.headers["set-cookie"]
    proto = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.9"}
    # A proxy on this machine, through the server's own proxy-header handling: believed.
    async with client(app, peer=LOCAL, behind=proxies.parse(None)) as api:
        assert "Secure" in (await api.post("/api/v1/auth/login", json=login_as, headers=proto)).headers["set-cookie"]
    # A proxy on a private network that was listed: believed. Not listed: it is just another peer.
    container = ("172.18.0.2", 40000)
    async with client(app, peer=container, behind=proxies.parse("172.18.0.0/16")) as api:
        assert "Secure" in (await api.post("/api/v1/auth/login", json=login_as, headers=proto)).headers["set-cookie"]
    async with client(app, peer=container, behind=proxies.parse(None)) as api:
        assert "Secure" not in (await api.post("/api/v1/auth/login", json=login_as, headers=proto)).headers["set-cookie"]
    # A stranger's header is never believed, listed proxies or not.
    async with client(app, peer=REMOTE, behind=proxies.parse("172.18.0.0/16")) as api:
        assert "Secure" not in (await api.post("/api/v1/auth/login", json=login_as, headers=proto)).headers["set-cookie"]


async def test_the_application_never_believes_a_forwarded_header_itself(app, factory):
    """Without the proxy-header handling in front (a different server, a mistake), a header from
    a loopback peer changes nothing: only Uvicorn's trusted list decides what is believed."""
    await set_owner(factory)
    proto = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.9"}
    async with client(app, peer=LOCAL) as api:
        response = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=proto)
        assert "Secure" not in response.headers["set-cookie"]
        for _ in range(limiter.login_lockout.max_failures):
            await api.post("/api/v1/auth/login", json={"passphrase": "wrong one"}, headers={"X-Forwarded-For": "198.51.100.9"})
        # counted against the socket's address, so another claimed visitor on the same socket is locked out too
        other = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers={"X-Forwarded-For": "198.51.100.77"})
        assert other.status_code == 429


async def test_each_visitor_behind_a_trusted_proxy_has_their_own_lockout(app, factory):
    from app.server import proxies

    await set_owner(factory)
    behind = proxies.parse(None)

    def forwarded(address):
        return {"X-Forwarded-For": address, "X-Forwarded-Proto": "https"}

    async with client(app, peer=LOCAL, behind=behind) as api:
        for _ in range(limiter.login_lockout.max_failures):
            await api.post("/api/v1/auth/login", json={"passphrase": "wrong one"}, headers=forwarded("198.51.100.1"))
        locked = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=forwarded("198.51.100.1"))
        assert locked.status_code == 429
        fresh = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=forwarded("198.51.100.2"))
        assert fresh.status_code == 200  # another visitor of the same proxy is not locked out
        # a client-supplied first hop does not choose the bucket: the proxy's own hop is the last one
        spoof = {"X-Forwarded-For": "198.51.100.50, 198.51.100.1"}
        assert (await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE}, headers=spoof)).status_code == 429


async def test_wrong_passphrases_lock_the_address_out(app, factory):
    await set_owner(factory)
    async with client(app) as api:
        for _ in range(limiter.login_lockout.max_failures):
            wrong = await api.post("/api/v1/auth/login", json={"passphrase": "not the passphrase"})
            assert wrong.status_code == 401 and error_code(wrong) == "wrong_passphrase"
        locked = await api.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})
        assert locked.status_code == 429 and error_code(locked) == "locked_out"
        assert int(locked.headers["retry-after"]) > 0
    async with client(app, peer=("198.51.100.20", 1)) as other:
        assert (await other.post("/api/v1/auth/login", json={"passphrase": PASSPHRASE})).status_code == 200


async def test_logout_revokes_the_session(app, factory):
    await set_owner(factory)
    async with client(app) as api:
        await login(api)
        token = api.cookies[COOKIE_NAME]
        assert (await api.post("/api/v1/auth/logout")).status_code == 204
        api.cookies.set(COOKIE_NAME, token, domain=PUBLIC_HOST)
        after = await api.get("/api/v1/auth/session")
        assert after.status_code == 401 and error_code(after) == "session_invalid"


async def test_changing_the_passphrase_signs_out_browsers(app, factory):
    await set_owner(factory)
    async with client(app) as api:
        await login(api)
        await set_owner(factory, "a different long passphrase")
        assert (await api.get("/api/v1/auth/session")).status_code == 401


# --- CSRF -----------------------------------------------------------------------


async def test_cookie_writes_need_the_csrf_token(app, factory):
    await set_owner(factory)
    async with client(app) as api:
        csrf = (await login(api))["csrf_token"]
        missing = await api.post("/api/v1/auth/pairing-codes", json={})
        assert missing.status_code == 403 and error_code(missing) == "csrf_failed"
        wrong = await api.post("/api/v1/auth/pairing-codes", json={}, headers={"X-CSRF-Token": "x" * 43})
        assert wrong.status_code == 403
        twice = await api.post("/api/v1/auth/pairing-codes", json={},
                               headers=[("X-CSRF-Token", csrf), ("X-CSRF-Token", csrf)])
        assert twice.status_code == 403
        assert (await api.delete("/api/v1/auth/devices/1")).status_code == 403
        ok = await api.post("/api/v1/auth/pairing-codes", json={}, headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 201
        # Reads need no token.
        assert (await api.get("/api/v1/auth/devices")).status_code == 200


# --- pairing ----------------------------------------------------------------------


async def test_pairing_code_exchanged_for_a_device_token(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        csrf = (await login(owner))["csrf_token"]
        code = await pairing_code(owner, csrf)
    async with client(app, peer=("198.51.100.30", 1)) as phone:
        paired = await phone.post("/api/v1/auth/pair", json={"code": credentials.format_pairing_code(code).lower(),
                                                             "label": "My phone"})
        assert paired.status_code == 200, paired.text
        token = paired.json()["token"]
        assert paired.json()["session"]["kind"] == "device"
        assert COOKIE_NAME not in phone.cookies
        session = await phone.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
        assert session.status_code == 200 and session.json()["label"] == "My phone"
        # A bearer write needs no CSRF token: nothing ambient carries it.
        created = await phone.post("/api/v1/auth/pairing-codes", json={}, headers={"Authorization": f"Bearer {token}"})
        assert created.status_code == 201
    async with factory() as db:
        stored = (await store.list_active_sessions(db))
        assert all(s.label != token for s in stored)
        rows = (await db.execute(AuthSession.__table__.select())).all()
        assert token not in str(rows) and credentials.digest(token) in str(rows)
        codes = (await db.execute(PairingCode.__table__.select())).all()
        assert code not in str(codes)


async def test_a_pairing_code_works_once(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"])
    async with client(app, peer=("198.51.100.31", 1)) as phone:
        assert (await phone.post("/api/v1/auth/pair", json={"code": code})).status_code == 200
        reused = await phone.post("/api/v1/auth/pair", json={"code": code})
        assert reused.status_code == 401 and error_code(reused) == "pairing_code_invalid"


async def test_an_expired_pairing_code_is_refused(app, factory, monkeypatch):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"])
    real_now = store.utcnow
    monkeypatch.setattr(store, "utcnow", lambda: real_now() + store.PAIRING_TTL + timedelta(seconds=1))
    async with client(app, peer=("198.51.100.32", 1)) as phone:
        assert (await phone.post("/api/v1/auth/pair", json={"code": code})).status_code == 401


async def test_pairing_brute_force_locks_the_address(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"])
    async with client(app, peer=("198.51.100.33", 1)) as attacker:
        for _ in range(limiter.pair_lockout.max_failures):
            assert (await attacker.post("/api/v1/auth/pair", json={"code": "ZZZZZZZZ"})).status_code == 401
        locked = await attacker.post("/api/v1/auth/pair", json={"code": code})
        assert locked.status_code == 429 and error_code(locked) == "locked_out"


async def test_wrong_guesses_from_many_addresses_kill_the_open_codes(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"])
    for n in range(store.MAX_PAIRING_FAILURES):
        async with client(app, peer=(f"198.51.100.{100 + n}", 1)) as guesser:
            assert (await guesser.post("/api/v1/auth/pair", json={"code": "ZZZZZZZZ"})).status_code == 401
    async with client(app, peer=("198.51.100.99", 1)) as phone:
        assert (await phone.post("/api/v1/auth/pair", json={"code": code})).status_code == 401


async def test_pairing_into_a_browser_cookie(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"])
    async with client(app, peer=("198.51.100.34", 1)) as browser:
        paired = await browser.post("/api/v1/auth/pair", json={"code": code, "cookie": True})
        assert paired.status_code == 200
        assert paired.json()["token"] is None and paired.json()["session"]["csrf_token"]
        assert (await browser.get("/api/v1/auth/session")).json()["kind"] == "browser"


async def test_a_browser_session_is_not_a_bearer_token_and_a_device_token_is_not_a_cookie(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        csrf = (await login(owner))["csrf_token"]
        cookie_token = owner.cookies[COOKIE_NAME]
        code = await pairing_code(owner, csrf)
    async with client(app, peer=("198.51.100.35", 1)) as other:
        as_bearer = await other.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {cookie_token}"})
        assert as_bearer.status_code == 401
        device_token = (await other.post("/api/v1/auth/pair", json={"code": code})).json()["token"]
        other.cookies.set(COOKIE_NAME, device_token, domain=PUBLIC_HOST)
        assert (await other.get("/api/v1/auth/session")).status_code == 401


async def test_a_chat_device_cannot_reach_admin_routes(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"], scope="chat")
    async with client(app, peer=("198.51.100.36", 1)) as phone:
        token = (await phone.post("/api/v1/auth/pair", json={"code": code})).json()["token"]
        bearer = {"Authorization": f"Bearer {token}"}
        session = await phone.get("/api/v1/auth/session", headers=bearer)
        assert session.status_code == 200 and session.json()["scopes"] == ["chat"]
        for method, path in (("GET", "/api/v1/auth/devices"), ("POST", "/api/v1/auth/pairing-codes"),
                             ("GET", "/api/v1/approval-rules"), ("PUT", "/api/v1/engines/claude/config")):
            denied = await phone.request(method, path, headers=bearer, json={})
            assert denied.status_code == 403 and error_code(denied) == "scope_denied", (method, path)


# --- devices ----------------------------------------------------------------------


async def test_devices_are_listed_and_a_revoked_token_stops_working(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        csrf = (await login(owner))["csrf_token"]
        code = await pairing_code(owner, csrf)
        async with client(app, peer=("198.51.100.37", 1)) as phone:
            token = (await phone.post("/api/v1/auth/pair", json={"code": code, "label": "Tablet"})).json()["token"]
            bearer = {"Authorization": f"Bearer {token}"}
            assert (await phone.get("/api/v1/auth/session", headers=bearer)).status_code == 200

            devices = (await owner.get("/api/v1/auth/devices")).json()
            assert {d["label"] for d in devices} >= {"Tablet"}
            assert [d["current"] for d in devices].count(True) == 1
            assert all("token" not in d and "token_hash" not in d for d in devices)
            tablet = next(d for d in devices if d["label"] == "Tablet")

            revoked = await owner.delete(f"/api/v1/auth/devices/{tablet['id']}", headers={"X-CSRF-Token": csrf})
            assert revoked.status_code == 204
            after = await phone.get("/api/v1/auth/session", headers=bearer)
            assert after.status_code == 401 and error_code(after) == "session_invalid"
            assert (await owner.delete(f"/api/v1/auth/devices/{tablet['id']}",
                                       headers={"X-CSRF-Token": csrf})).status_code == 404
            assert all(d["label"] != "Tablet" for d in (await owner.get("/api/v1/auth/devices")).json())


async def test_revoking_the_current_browser_clears_its_cookie(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        csrf = (await login(owner))["csrf_token"]
        me = next(d for d in (await owner.get("/api/v1/auth/devices")).json() if d["current"])
        response = await owner.delete(f"/api/v1/auth/devices/{me['id']}", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 204 and "Max-Age=0" in response.headers["set-cookie"]


# --- web identity -----------------------------------------------------------------


async def test_web_identity_comes_from_the_session_not_the_browser(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        csrf = {"X-CSRF-Token": (await login(owner))["csrf_token"]}
        assert (await owner.get("/api/v1/auth/session")).json()["user_id"] is None
        legacy = "web_0f8fad5b-d9cb-469f-a165-70867728950e"
        moved = await owner.put("/api/v1/auth/identity", json={"legacy_user_id": legacy}, headers=csrf)
        assert moved.json()["user_id"] == legacy
        # A second browser's old id does not replace it.
        other = await owner.put("/api/v1/auth/identity", json={"legacy_user_id": "123456"}, headers=csrf)
        assert other.json()["user_id"] == legacy
        bad = await owner.put("/api/v1/auth/identity", json={"legacy_user_id": "anything; drop"}, headers=csrf)
        assert bad.status_code == 422
        assert (await owner.get("/api/v1/auth/session")).json()["user_id"] == legacy

        unclaimed = await owner.put("/api/v1/auth/identity", json={"link_code": "TSTXYZ"}, headers=csrf)
        assert unclaimed.status_code == 409
        async with factory() as db:
            db.add(LinkCode(code="TSTABC", user_id="987654321", expires_at=store.utcnow() + timedelta(minutes=5),
                            claimed_at=store.utcnow()))
            await db.commit()
        linked = await owner.put("/api/v1/auth/identity", json={"link_code": "tstabc"}, headers=csrf)
        assert linked.json()["user_id"] == "987654321"

        cleared = await owner.delete("/api/v1/auth/identity", headers=csrf)
        assert cleared.json()["user_id"] is None
        guest = await owner.put("/api/v1/auth/identity", json={"guest": True}, headers=csrf)
        assert guest.json()["user_id"].startswith("web_")
        both = await owner.put("/api/v1/auth/identity", json={"guest": True, "link_code": "X"}, headers=csrf)
        assert both.status_code == 422


# --- desktop and local execution -----------------------------------------------------


async def test_the_desktop_profile_steps_aside_for_the_launch_token(app, monkeypatch):
    """Under the desktop profile LaunchAuthMiddleware is the gate; this one adds nothing."""
    from app.auth import middleware

    monkeypatch.setattr(middleware.profile, "active", lambda: True)
    async with client(app, peer=LOCAL, base_url="http://127.0.0.1:51234") as api:
        response = await api.get("/api/v1/auth/session")
        assert response.status_code == 200 and response.json()["kind"] == "desktop"


def test_local_access_accepts_an_authenticated_session_and_keeps_the_loopback_rules(monkeypatch):
    from starlette.requests import Request

    from app.auth.middleware import set_principal
    from app.auth.policy import Principal
    from app.services.local_access_policy import LocalAccessDenied, LocalAccessGrant, LoopbackPolicy

    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    remote = {"type": "http", "client": REMOTE, "headers": [(b"host", PUBLIC_HOST.encode())]}
    with pytest.raises(LocalAccessDenied):
        LoopbackPolicy().authorize(Request(dict(remote)))
    signed_in = dict(remote)
    set_principal(signed_in, Principal(kind="device", scopes=("chat",), session_id=1))
    assert isinstance(LoopbackPolicy().authorize(Request(signed_in)), LocalAccessGrant)
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    with pytest.raises(LocalAccessDenied):
        LoopbackPolicy().authorize(Request(signed_in))
