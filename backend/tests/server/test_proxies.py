"""The trusted-proxy list (app/server/proxies.py) and how the server hands it to Uvicorn."""

import ipaddress

import pytest
from uvicorn.middleware.proxy_headers import _TrustedHosts

from app.server import entry, proxies
from app.server.entry import ServerConfigError, read_options

LOOPBACK = ("127.0.0.1", "::1")


def test_by_default_exactly_the_two_loopback_addresses_are_trusted():
    assert proxies.parse(None) == LOOPBACK
    assert proxies.parse("") == LOOPBACK
    assert proxies.parse("   ") == LOOPBACK
    assert proxies.LOOPBACK == LOOPBACK


def test_listed_addresses_and_networks_are_added_in_canonical_form():
    assert proxies.parse("172.18.0.0/16") == (*LOOPBACK, "172.18.0.0/16")
    assert proxies.parse(" 10.0.0.5 , fd00::/8,10.0.0.5") == (*LOOPBACK, "10.0.0.5", "fd00::/8")
    assert proxies.parse("::FFFF:10.0.0.5") == (*LOOPBACK, str(ipaddress.ip_address("::ffff:10.0.0.5")))
    assert proxies.parse("127.0.0.1") == LOOPBACK  # already there, not listed twice


@pytest.mark.parametrize("bad", [
    "*", "172.18.0.0/16,*", "0.0.0.0/0", "::/0", "172.18.0.0/0",
    "proxy.internal", "localhost", "172.18.0.2:8080", "172.18.0.1/16", "172.18.0.0/33", "not-an-address",
    "172.18.0.0/16,", ",172.18.0.0/16", "172.18.0.0/16,,10.0.0.5", "172.18.0.0/16 10.0.0.5", "10.0.0.5;10.0.0.6",
])
def test_anything_unclear_or_too_wide_is_refused(bad):
    with pytest.raises(proxies.ProxyListError) as caught:
        proxies.parse(bad)
    assert proxies.ENV_NAME in str(caught.value)


def test_the_wide_open_forms_say_why():
    with pytest.raises(proxies.ProxyListError, match="anyone who reaches the port"):
        proxies.parse("*")
    with pytest.raises(proxies.ProxyListError, match="every address"):
        proxies.parse("0.0.0.0/0")
    with pytest.raises(proxies.ProxyListError, match="network address"):
        proxies.parse("172.18.0.1/16")


def test_uvicorn_trusts_what_the_list_says_and_nothing_else():
    """The parsed list is what Uvicorn is given, so check it with Uvicorn's own matcher."""
    trusted = _TrustedHosts(",".join(proxies.parse("172.18.0.0/16,10.0.0.5")))
    for peer in ("127.0.0.1", "::1", "172.18.0.2", "172.18.255.254", "10.0.0.5"):
        assert peer in trusted, peer
    for peer in ("127.0.0.2", "172.19.0.1", "10.0.0.6", "203.0.113.7", "192.168.1.1", "unknown", ""):
        assert peer not in trusted, peer


def test_the_default_matches_what_uvicorn_trusts_with_no_list():
    default = _TrustedHosts(",".join(proxies.parse(None)))
    assert "127.0.0.1" in default and "::1" in default and "127.0.0.2" not in default


# -- the environment variable Uvicorn would read ----------------------------------------------------

def test_forwarded_allow_ips_from_the_environment_is_reported_as_ignored():
    assert proxies.environment_override({}, LOOPBACK) is None
    assert proxies.environment_override({"FORWARDED_ALLOW_IPS": ""}, LOOPBACK) is None
    assert proxies.environment_override({"FORWARDED_ALLOW_IPS": "127.0.0.1,::1"}, LOOPBACK) is None
    said = proxies.environment_override({"FORWARDED_ALLOW_IPS": "*"}, LOOPBACK)
    assert said and "ignored" in said and "TRUSTED_PROXIES" in said and "'*'" in said


# -- the options the server starts with ---------------------------------------------------------------

@pytest.fixture
def ui(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    return str(tmp_path)


def test_the_server_options_carry_the_list(ui):
    assert read_options([], {"OPEN_DOTS_UI_DIR": ui}).trusted_proxies == LOOPBACK
    options = read_options([], {"OPEN_DOTS_UI_DIR": ui, "TRUSTED_PROXIES": "172.18.0.0/16"})
    assert options.trusted_proxies == (*LOOPBACK, "172.18.0.0/16")


@pytest.mark.parametrize("bad", ["*", "0.0.0.0/0", "proxy.internal"])
def test_a_bad_list_stops_the_server_before_it_starts(ui, bad):
    with pytest.raises(ServerConfigError, match="TRUSTED_PROXIES"):
        read_options([], {"OPEN_DOTS_UI_DIR": ui, "TRUSTED_PROXIES": bad})


def test_main_refuses_a_bad_list_with_the_usage_exit_code(ui, capsys):
    code = entry.main([], {"OPEN_DOTS_UI_DIR": ui, "TRUSTED_PROXIES": "*"})
    assert code == entry.EXIT_USAGE
    assert "TRUSTED_PROXIES" in capsys.readouterr().err


def test_uvicorn_is_started_with_exactly_the_list_and_never_the_environment(ui, monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(entry, "check_schema", lambda: None)
    monkeypatch.setattr(entry, "build_app", lambda options: object())
    environment = {"OPEN_DOTS_UI_DIR": ui, "TRUSTED_PROXIES": "172.18.0.0/16", "FORWARDED_ALLOW_IPS": "*"}
    assert entry.main([], environment) == 0
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "127.0.0.1,::1,172.18.0.0/16"  # not "*"

    seen.clear()
    assert entry.main([], {"OPEN_DOTS_UI_DIR": ui}) == 0
    assert seen["forwarded_allow_ips"] == "127.0.0.1,::1"
