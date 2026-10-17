"""Passphrase hashing, codes, CSRF values, lockout and request classification."""

import pytest

from app.auth import credentials, limiter, request_info
from app.auth.policy import is_public, required_scope


def test_passphrase_round_trip_and_wrong_passphrase():
    stored = credentials.hash_passphrase("correct horse battery")
    assert stored.startswith("scrypt$")
    assert "correct horse" not in stored
    assert credentials.verify_passphrase("correct horse battery", stored)
    assert not credentials.verify_passphrase("correct horse batterY", stored)


def test_each_hash_has_its_own_salt():
    assert credentials.hash_passphrase("same passphrase") != credentials.hash_passphrase("same passphrase")


@pytest.mark.parametrize("stored", [None, "", "plain", "bcrypt$1$2$3$a$b", "scrypt$x$8$1$aa$bb",
                                     "scrypt$1073741824$8$1$aa$bb", "scrypt$65536$8$1$!!$bb"])
def test_a_malformed_or_hostile_hash_never_matches(stored):
    assert not credentials.verify_passphrase("anything at all", stored)


def test_passphrase_strength_limits():
    with pytest.raises(credentials.WeakPassphrase):
        credentials.check_passphrase_strength("short")
    with pytest.raises(credentials.WeakPassphrase):
        credentials.check_passphrase_strength("x" * (credentials.MAX_PASSPHRASE_LENGTH + 1))
    credentials.check_passphrase_strength("long enough phrase")


def test_pairing_codes_are_random_unambiguous_and_normalized():
    codes = {credentials.new_pairing_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert len(code) == credentials.PAIRING_CODE_LENGTH
        assert set(code) <= set(credentials.PAIRING_ALPHABET)
    assert not set("01ILO") & set(credentials.PAIRING_ALPHABET)
    code = "ABCD2345"
    assert credentials.normalize_pairing_code(" abcd-2345 ") == code
    assert credentials.format_pairing_code(code) == "ABCD-2345"


def test_session_tokens_and_csrf_values():
    token = credentials.new_session_token()
    assert credentials.looks_like_session_token(token)
    assert not credentials.looks_like_session_token("abc")
    assert credentials.csrf_token_for(token) == credentials.csrf_token_for(token)
    assert credentials.csrf_token_for(token) != credentials.csrf_token_for(credentials.new_session_token())
    assert credentials.digest(token) != token and len(credentials.digest(token)) == 64


def test_lockout_locks_after_the_limit_and_unlocks_later():
    now = [1000.0]
    lock = limiter.Lockout(max_failures=3, window_seconds=60, lock_seconds=120, clock=lambda: now[0])
    for _ in range(2):
        lock.failure("a")
    assert lock.retry_after("a") == 0
    lock.failure("a")
    assert 0 < lock.retry_after("a") <= 121
    assert lock.retry_after("b") == 0
    now[0] += 121
    assert lock.retry_after("a") == 0


def test_lockout_forgets_failures_outside_the_window_and_on_success():
    now = [0.0]
    lock = limiter.Lockout(max_failures=2, window_seconds=10, lock_seconds=10, clock=lambda: now[0])
    lock.failure("a")
    now[0] += 11
    lock.failure("a")
    assert lock.retry_after("a") == 0
    lock.success("a")
    lock.failure("a")
    assert lock.retry_after("a") == 0


def test_rate_limit():
    now = [0.0]
    rate = limiter.RateLimit(limit=2, window_seconds=10, clock=lambda: now[0])
    assert rate.allow("a") and rate.allow("a") and not rate.allow("a")
    now[0] += 11
    assert rate.allow("a")


def scope(headers=(), client=("127.0.0.1", 1), scheme="http"):
    return {"type": "http", "scheme": scheme, "client": client,
            "headers": [(k.encode(), v.encode()) for k, v in headers]}


def test_hostname_parsing():
    assert request_info.hostname_of("LOCALHOST:8000") == "localhost"
    assert request_info.hostname_of("[::1]:8000") == "[::1]"
    assert request_info.hostname_of("::1") == ""
    assert request_info.host_is_trusted("app.example.com", ["*.example.com"])
    assert not request_info.host_is_trusted("example.com", ["*.example.com"])
    assert not request_info.host_is_trusted("evilexample.com", ["*.example.com"])


def test_loopback_eligibility():
    assert request_info.loopback_eligible(scope([("host", "localhost:8000")]))
    assert request_info.loopback_eligible(scope([("host", "127.0.0.1:8000"), ("origin", "http://localhost:3000")]))
    assert not request_info.loopback_eligible(scope([("host", "localhost:8000")], client=("10.0.0.2", 1)))
    assert not request_info.loopback_eligible(scope([("host", "dots.example.com")]))
    assert not request_info.loopback_eligible(scope([("host", "localhost"), ("x-forwarded-for", "1.2.3.4")]))
    assert not request_info.loopback_eligible(scope([("host", "localhost"), ("forwarded", "for=1.2.3.4")]))
    assert not request_info.loopback_eligible(scope([("host", "localhost"), ("origin", "https://evil.example")]))
    assert not request_info.loopback_eligible(scope([("host", "localhost"), ("host", "localhost")]))


def test_client_source_takes_only_the_last_hop_from_a_local_proxy():
    assert request_info.client_source(scope([("x-forwarded-for", "6.6.6.6, 5.5.5.5")])) == "5.5.5.5"
    # From a remote peer the header is the client's own claim: ignored.
    assert request_info.client_source(scope([("x-forwarded-for", "127.0.0.1")], client=("9.9.9.9", 1))) == "9.9.9.9"


def test_https_detection_trusts_forwarded_proto_only_from_a_local_proxy():
    assert request_info.is_https(scope(scheme="https"))
    assert request_info.is_https(scope([("x-forwarded-proto", "https")]))
    assert not request_info.is_https(scope([("x-forwarded-proto", "https")], client=("9.9.9.9", 1)))


def test_route_policy_is_default_deny():
    assert is_public("GET", "/api/v1/health")
    assert is_public("POST", "/api/v1/auth/pair")
    assert not is_public("GET", "/api/v1/auth/devices")
    assert not is_public("GET", "/docs")
    assert required_scope("GET", "/api/v1/threads") == "chat"
    assert required_scope("POST", "/api/v1/threads/3/turns") == "chat"
    assert required_scope("POST", "/api/v1/auth/pairing-codes") == "admin"
    assert required_scope("PUT", "/api/v1/engines/claude/config") == "admin"
    assert required_scope("GET", "/api/v1/some-future-route") == "admin"
