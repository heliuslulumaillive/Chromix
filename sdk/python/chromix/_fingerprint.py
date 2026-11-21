"""Public fingerprint-switch syntax shared by all Python launch paths."""
from __future__ import annotations

import math
import re

OFF_VALUES = frozenset(("off", "false", "0", "disable", "disabled"))
_BRANDS = {"chrome": "Chrome", "google chrome": "Chrome", "edge": "Edge",
           "microsoft edge": "Edge", "opera": "Opera", "vivaldi": "Vivaldi"}
_BOOLEANS = {"--fingerprint-noise", "--fingerprint-allow-3p-cookies"}
_SURFACE_MODES = {f"{surface}-mode" for surface in
                  ("canvas", "webgl", "audio", "fonts", "rects", "text", "sensors", "media")}
_RETIRED = {"fonts-mode-seed", "fonts-amount", "fonts-allowlist", "fonts-local-allowlist", "fonts-experimental-packages", "hdr", "widevine-cdm", "sapi-voices", "voices", "audio-render", "font-policy", "font-whitelist",
            "windows-font-metrics", "keyboard-layout"}
_INTEGERS = {
    "--fingerprint-hardware-concurrency": (1, 128),
    "--fingerprint-screen-width": (1, 32768),
    "--fingerprint-screen-height": (1, 32768),
    "--fingerprint-taskbar-height": (0, 32768),
    "--fingerprint-storage-quota": (0, ((1 << 63) - 1) // (1024 * 1024)),
}
_PREFERENCE_BOOLEANS = {"reduced-motion", "reduced-transparency", "inverted-colors"}
_PREFERENCE_INTEGERS = {"max-touch-points": (0, 16), "timer-resolution": (0, 1000),
                        "audio-seed": (1, (1 << 64) - 1)}
_PREFERENCE_ENUMS = {
    "gpu-backend": {"native", "compatibility"},
    "pixel-noise": {"native", "seeded"},
    "color-scheme": {"dark", "light"},
    "preferred-contrast": {"more", "less", "no-preference"},
    "forced-colors": {"active", "none", "true", "false", "1", "0"},
    "pointer": {"fine", "coarse", "none"},
    "hover": {"hover", "none"},
}
_CODECS = {"codec-h264", "codec-vp8", "codec-vp9", "codec-av1", "codec-hevc"}


def _valid_codec(value):
    if value in ("", "native", "disabled"):
        return True
    tokens = value.split(",")
    return "supported" in tokens and set(tokens) <= {"supported", "smooth", "power-efficient"}


def _preference(key):
    for prefix in ("--fingerprint-", "--uxr-"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return ""


def _validate_preferences(args, *, final=False):
    # Match Chromium's last-switch semantics and native-alias precedence.
    switches = dict(arg.partition("=")[::2] for arg in args)
    def value(name):
        return switches.get("--uxr-" + name, switches.get("--fingerprint-" + name))
    pointer, hover, touch = value("pointer"), value("hover"), value("max-touch-points")
    touch = int(touch) if touch is not None else None
    if ((pointer == "none" and touch is not None and touch > 0)
            or (pointer == "coarse" and touch == 0)
            or (pointer == "none" and hover == "hover")):
        raise ValueError("inconsistent pointer, hover and max-touch-points")
    if (final and value("pixel-noise") == "seeded" and not fingerprint_off(args)
            and switches.get("--fingerprint-noise") != "false"
            and "--uxr-disable-fingerprint-noise" not in switches):
        seed = switches.get("--uxr-canvas-seed")
        if seed is None:
            if "--fingerprint" not in switches:
                raise ValueError("seeded pixel-noise requires --fingerprint or a nonzero canvas seed")
            # Patch 0036 derives absent canvas seeds only with a public fingerprint switch.
            seed = switches["--fingerprint"] or switches.get("--uxr-fingerprint-seed") or None
        if seed is not None and (not re.fullmatch(r"[0-9]+", seed) or not 1 <= int(seed) < (1 << 64)):
            raise ValueError("seeded pixel-noise requires a nonzero uint64 canvas seed")


def fingerprint_off(args):
    modes = [a.partition("=")[2].lower() for a in args if a.partition("=")[0] == "--fingerprint"]
    return bool(modes and modes[-1] in OFF_VALUES)


def normalize_fingerprint_args(args, *, final=False):
    result = []
    for arg in args or []:
        if not isinstance(arg, str) or "\0" in arg:
            raise ValueError("browser arguments must be strings without NUL")
        key, equal, value = arg.partition("=")
        preference = _preference(key)
        if preference in _RETIRED or re.match(r"^(?:offset|voices)-mode(?:-|$)", preference):
            raise ValueError(f"Retired browser option: {key}")
        if preference in _SURFACE_MODES and value != "native" and (preference == "fonts-mode" or value != "shardx"):
            raise ValueError(f"Unsupported surface mode: {key}")
        if preference in _PREFERENCE_BOOLEANS:
            low = value.lower()
            if low in OFF_VALUES:
                arg = key + "=false"
            elif low in ("", "true", "1", "on", "enable", "enabled"):
                arg = key + "=true"
            else:
                raise ValueError(f"{key} requires a boolean")
        elif preference in _PREFERENCE_INTEGERS:
            minimum, maximum = _PREFERENCE_INTEGERS[preference]
            if not re.fullmatch(r"[0-9]+", value) or not minimum <= int(value) <= maximum:
                raise ValueError(f"{key} requires an integer in [{minimum}, {maximum}]")
        elif preference in _PREFERENCE_ENUMS:
            if value not in _PREFERENCE_ENUMS[preference]:
                raise ValueError(f"{key} requires one of {', '.join(sorted(_PREFERENCE_ENUMS[preference]))}")
        elif preference in _CODECS:
            if not _valid_codec(value):
                raise ValueError(f"{key} requires native, disabled or supported[,smooth][,power-efficient]")
        elif key == "--fingerprint":
            if value.lower() in OFF_VALUES:
                arg = "--fingerprint=off"
            elif value and (not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) < (1 << 64)):
                raise ValueError("--fingerprint requires a uint64 seed or off/false/0/disable/disabled")
        elif key == "--fingerprint-brand":
            brand = _BRANDS.get(value.lower())
            if brand is None:
                raise ValueError("--fingerprint-brand must be Chrome, Edge, Opera or Vivaldi")
            arg = key + "=" + brand
        elif key in _BOOLEANS:
            low = value.lower()
            if low in OFF_VALUES:
                arg = key + "=false"
            elif low in ("", "true", "1", "on", "enable", "enabled"):
                arg = key + "=true"
            else:
                raise ValueError(f"{key} requires a boolean")
        elif key in _INTEGERS:
            minimum, maximum = _INTEGERS[key]
            if not re.fullmatch(r"[0-9]+", value) or not minimum <= int(value) <= maximum:
                raise ValueError(f"{key} requires an integer in [{minimum}, {maximum}]")
        elif key == "--fingerprint-device-memory":
            try:
                valid = bool(re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value))
                valid = valid and math.isfinite(float(value)) and 0 < float(value) <= 32
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("--fingerprint-device-memory requires a number greater than 0 and at most 32")
        elif key in ("--fingerprint-brand-version", "--fingerprint-platform-version"):
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value) or any(int(n) > 0xffffffff for n in value.split(".")):
                raise ValueError(f"{key} requires a numeric version with at most four components")
            if key == "--fingerprint-brand-version" and not 1 <= int(value.split(".")[0]) <= 0x7fffffff:
                raise ValueError(f"{key} requires a positive int32 major version")
        result.append(arg)
    if fingerprint_off(result):
        result = [arg for arg in result if arg.partition("=")[0] != "--fingerprint-platform"]
    _validate_preferences(result, final=final)
    return result
