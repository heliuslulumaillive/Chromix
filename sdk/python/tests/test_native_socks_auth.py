"""Browser-facing SOCKS validation, including rejection before driver startup."""
import os
import pytest
from chromix._socks_auth import browser_proxy
from chromix._fonts import apply_font_env
from chromix.api import _resolve_proxy_config


@pytest.mark.parametrize("value", ["socks5://user:password@localhost:1080",
    {"server":"socks5://[::1]:1080","username":"user","password":"password"}])
def test_authentication_requires_a_bridge(value):
    for parse in (browser_proxy, _resolve_proxy_config):
        with pytest.raises(ValueError, match="local proxy bridge"):
            parse(value)


def test_ordinary_proxy_routes():
    assert browser_proxy("socks5://localhost:1080") == {"server":"socks5://localhost:1080"}
    assert browser_proxy("http://u:p@localhost:8080") == {"server":"http://localhost:8080","username":"u","password":"p"}


def test_obsolete_auth_is_removed_from_children(monkeypatch):
    key = "CHROMIX_SOCKS5_AUTH"
    monkeypatch.setenv(key, "fixture")
    launch = {"env":{"KEEP":"yes"}}
    apply_font_env("fixture", launch)
    assert launch["env"]["KEEP"] == "yes" and key not in launch["env"]
    assert os.environ[key] == "fixture"
    with pytest.raises(ValueError, match="retired"):
        apply_font_env("fixture", {"env":{"chromix_socks5_auth":"explicit"}})
