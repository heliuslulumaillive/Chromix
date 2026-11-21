"""Browser proxy validation and removal of obsolete launch-scoped credentials."""
from urllib.parse import urlsplit
from ._network import split_proxy

_RETIRED_AUTH_ENV = "CHROMIX_SOCKS5_AUTH"


def browser_proxy(value):
    proxy = split_proxy(value)
    if (proxy and urlsplit(proxy["server"]).scheme in ("socks", "socks5", "socks5h") and
            any(key in proxy for key in ("username", "password"))):
        raise ValueError("Authenticated SOCKS5 requires a local proxy bridge")
    return proxy


def has_retired_proxy_auth(env):
    return any(key.upper() == _RETIRED_AUTH_ENV for key in (env or {}))


def without_retired_proxy_auth(env):
    return {key: value for key, value in env.items() if key.upper() != _RETIRED_AUTH_ENV}
