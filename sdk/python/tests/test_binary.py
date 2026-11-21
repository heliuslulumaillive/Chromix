"""Offline download/extraction integration tests using real ZIP fixtures."""
import hashlib
import io
import json
import os
import stat
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chromix import _binary as binary, api  # noqa: E402

HOST = "https://fixtures.invalid/release"
TAG = binary._CHANNELS["stable"]["tag"]
PLATFORMS = [
    ("Linux", "x86_64", "linux-x64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Windows", "AMD64", "win-x64"),
    ("Windows", "ARM64", "win-arm64"),
    ("Darwin", "x86_64", "mac-x64"),
    ("Darwin", "arm64", "mac-arm64"),
]


def file(name, data=b"fixture", mode=stat.S_IFREG | 0o644):
    return name, data, mode


def link(name, target):
    return file(name, target.encode(), stat.S_IFLNK | 0o777)


def zip_fixture(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload, mode in entries:
            info = zipfile.ZipInfo(name)
            # Preserve raw names on disk even when ZipInfo normalizes them on Windows.
            info.filename = name
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return data.getvalue()


@pytest.fixture(params=[False, True], ids=["native-host", "windows-host"])
def zip_name_normalization(request, monkeypatch):
    if request.param:
        init = zipfile.ZipInfo.__init__

        def windows_init(self, *args, **kwargs):
            init(self, *args, **kwargs)
            self.filename = self.filename.replace("\\", "/")

        monkeypatch.setattr(zipfile.ZipInfo, "__init__", windows_init)
    return request.param


def bundle(plat):
    return [file("chromix/", b"", stat.S_IFDIR | 0o755), file(binary._ASSETS[plat][2]),
            file(binary._binary_path(plat, Path(".")).as_posix(), b"chrome fixture"),
            file("chromix/helper", b"helper", stat.S_IFREG | 0o4755),
            file("chromix/resources.pak", b"resources")]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(binary, "_CACHE", root)
    monkeypatch.setattr(api, "_CACHE", root)
    monkeypatch.setenv("CHROMIX_DOWNLOAD_HOST", HOST)
    for name in ("CLOAKBROWSER_BINARY_PATH", "CLOAKBROWSER_VERSION", "CLOAKBROWSER_RELEASE_CHANNEL"):
        monkeypatch.delenv(name, raising=False)
    return root


def mock_release(monkeypatch, plat, data, failure=""):
    urls = []

    def retrieve(url, target):
        urls.append(url)
        assert url == f"{HOST}/{binary._ASSETS[plat][0]}"
        if failure == "http":
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
        if failure == "network":
            raise urllib.error.URLError("network down")
        Path(target).write_bytes(data[:10] if failure == "stream" else data)
        if failure == "stream":
            raise OSError("stream interrupted")
        return target, {}

    def urlopen(url, timeout):
        urls.append(url)
        assert url == f"{HOST}/SHA256SUMS"
        assert timeout == 30
        if failure == "manifest":
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
        digest = "0" * 64 if failure == "checksum" else hashlib.sha256(data).hexdigest()
        return io.BytesIO(f"{digest.upper()} *{binary._ASSETS[plat][0]}\n".encode())

    monkeypatch.setattr(binary.urllib.request, "urlretrieve", retrieve)
    monkeypatch.setattr(binary.urllib.request, "urlopen", urlopen)
    return urls


@pytest.mark.parametrize("system,machine,plat", PLATFORMS)
def test_zip_download_public_api_and_cache(cache, monkeypatch, system, machine, plat):
    monkeypatch.setattr(binary.platform, "system", lambda: system)
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    assert binary.resolve_platform() == plat
    assert binary._ASSETS[plat][:2] == (f"chromix-{plat}.zip", "zip")
    assert not api.binary_info(release_channel="stable")["installed"]
    tag = "v155.0.8059.39" if plat.startswith("linux-") else "v154.0.8037.97" if plat.startswith("win-") else TAG
    root = cache / tag / plat
    chrome = binary._binary_path(plat, root)
    assert api.ensure_binary(release_channel="stable") == chrome
    assert chrome.read_bytes() == b"chrome fixture"
    info = api.binary_info(release_channel="stable")
    assert info["platform"] == plat
    assert info["installed"] and info["path"] == str(chrome)
    assert binary._download(plat, HOST, tag) == root / binary._ASSETS[plat][2]
    assert api.ensure_binary(release_channel="stable") == chrome
    assert urls == [f"{HOST}/{binary._ASSETS[plat][0]}", f"{HOST}/SHA256SUMS"]
    assert list((cache / tag).iterdir()) == [root]
    assert not (root / binary._ASSETS[plat][0]).exists()
    if plat.startswith("mac-"):
        assert chrome.relative_to(root).as_posix() == "chromix/Chromium.app/Contents/MacOS/Chromium"
    if os.name != "nt":
        for path in (chrome, root / binary._ASSETS[plat][2], root / "chromix/helper"):
            assert stat.S_IMODE(path.stat().st_mode) == 0o755
        assert stat.S_IMODE((root / "chromix/resources.pak").stat().st_mode) == 0o644


@pytest.mark.parametrize("plat", ["win-x64", "win-arm64"])
@pytest.mark.parametrize("directories", [False, True])
def test_windows_backslash_zip_download(cache, monkeypatch, plat, directories, zip_name_normalization):
    entries = [file(r"chromix\chromix.cmd", mode=0), file(r"chromix\chrome.exe", b"chrome fixture", mode=0),
               file(r"chromix\locales/en-US.pak", b"locale", mode=0)]
    if directories:
        entries = [file("chromix\\", b"", mode=0), file("chromix\\locales\\", b"", mode=0),
                   file("chromix\\empty\\", b"", mode=0)] + entries
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    urls = mock_release(monkeypatch, plat, zip_fixture(entries))
    chrome = api.ensure_binary()
    root = chrome.parent.parent
    assert chrome.read_bytes() == b"chrome fixture"
    assert (root / "chromix/locales/en-US.pak").read_bytes() == b"locale"
    if directories:
        assert (root / "chromix/empty").is_dir()
    assert all("\\" not in path.name for path in root.rglob("*"))
    assert api.binary_info()["installed"]
    assert api.ensure_binary() == chrome
    assert len(urls) == 2


@pytest.mark.parametrize("plat", [None, "linux-x64", "linux-arm64", "mac-x64", "mac-arm64"])
@pytest.mark.parametrize("name", [r"chromix\helper", r"chromix/locales\en-US.pak", "chromix\\empty\\"])
def test_posix_and_default_extraction_reject_backslash(tmp_path, plat, name, zip_name_normalization):
    archive = tmp_path / "input.zip"
    archive.write_bytes(zip_fixture([file(name)]))
    with zipfile.ZipFile(archive) as z:
        entry, = z.infolist()
        assert entry.orig_filename == name
        if zip_name_normalization or os.sep == "\\":
            assert entry.filename == name.replace("\\", "/")
    with pytest.raises(ValueError, match="Unsafe ZIP path"):
        if plat is None:
            binary._extract_zip(archive, tmp_path / "output")
        else:
            binary._extract_zip(archive, tmp_path / "output", plat)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("plat", [None, *binary._ASSETS])
def test_extraction_rejects_nul_before_filename_truncation(tmp_path, plat, zip_name_normalization):
    archive = tmp_path / "input.zip"
    name = "chromix/helper\x00hidden"
    archive.write_bytes(zip_fixture([file(name)]))
    with zipfile.ZipFile(archive) as z:
        entry, = z.infolist()
        assert entry.orig_filename == name
        assert entry.filename == "chromix/helper"
    with pytest.raises(ValueError, match="Unsafe ZIP path"):
        binary._extract_zip(archive, tmp_path / "output", plat)
    assert not (tmp_path / "output").exists()


WINDOWS_UNSAFE_ENTRIES = {
    "mixed-duplicate": [file(r"chromix\chrome.exe")],
    "mixed-case-collision": [file(r"chromix\CHROME.EXE")],
    "directory-collision": [file("chromix\\", b"", mode=0)],
    "traversal": [file(r"chromix\..\..\outside")],
    "mixed-traversal": [file(r"chromix/locales\../outside")],
    "dot": [file(r"chromix\.\helper")],
    "empty-component": [file(r"chromix\\helper")],
    "unc": [file(r"\\server\chromix\helper")],
    "absolute": [file(r"\chromix\helper")],
    "drive": [file(r"C:\chromix\helper")],
    "drive-relative": [file(r"C:chromix\helper")],
    "ads": [file(r"chromix\chrome.exe:stream")],
    "device": [file(r"chromix\NUL.txt")],
    "trailing-dot": [file(r"chromix\helper.")],
    "trailing-space": [file("chromix\\helper ")],
    "link-escape": [link(r"chromix\link", "../../outside")],
    "link-backslash-escape": [link(r"chromix\link", r"..\..\outside")],
    "link-unc": [link(r"chromix\link", r"\\server\outside")],
    "link-drive": [link(r"chromix\link", r"C:\outside")],
    "link-traversal": [link(r"chromix\link", "../.."), file(r"chromix\link/outside")],
    "link-case-traversal": [link(r"chromix\Link", "../.."), file(r"chromix/link\outside")],
}


@pytest.mark.parametrize("plat", ["win-x64", "win-arm64"])
@pytest.mark.parametrize("name,entries", WINDOWS_UNSAFE_ENTRIES.items(), ids=WINDOWS_UNSAFE_ENTRIES)
def test_windows_backslash_zip_rejects_unsafe_paths(cache, monkeypatch, plat, name, entries, zip_name_normalization):
    outside = cache / "outside"
    outside.write_text("untouched")
    mock_release(monkeypatch, plat, zip_fixture(bundle(plat) + entries))
    with pytest.raises((ValueError, OSError)):
        binary._download(plat, HOST, TAG)
    assert outside.read_text() == "untouched"
    assert list((cache / TAG).iterdir()) == []


@pytest.mark.parametrize("machine,plat", [
    ("AMD64", "win-x64"), ("x86_64", "win-x64"),
    ("ARM64", "win-arm64"), ("arm64", "win-arm64"), ("aarch64", "win-arm64"),
])
def test_windows_platform_and_paths(monkeypatch, tmp_path, machine, plat):
    monkeypatch.setattr(binary.platform, "system", lambda: "Windows")
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    assert binary.resolve_platform() == plat
    assert binary._ASSETS[plat] == (f"chromix-{plat}.zip", "zip", "chromix/chromix.cmd")
    assert binary._binary_path(plat, tmp_path) == tmp_path / "chromix/chrome.exe"


@pytest.mark.parametrize("system,machine", [
    ("Linux", "i686"), ("Windows", "armv7l"), ("Windows", "i686"),
    ("Windows", "unknown"), ("Darwin", "ppc"), ("FreeBSD", "amd64"),
])
def test_unsupported_platform(monkeypatch, system, machine):
    monkeypatch.setattr(binary.platform, "system", lambda: system)
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    assert binary.resolve_platform() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
def test_framework_symlinks_and_parent_relative_targets(cache, monkeypatch):
    plat = "mac-arm64"
    entries = bundle(plat) + [
        file("chromix/Framework/Versions/A/Library", b"library", stat.S_IFREG | 0o755),
        link("chromix/Framework/Library", "Versions/Current/Library"),
        link("chromix/Framework/Versions/Current", "A"),
        link("chromix/Framework/chrome", "../Chromium.app/Contents/MacOS/Chromium"),
    ]
    mock_release(monkeypatch, plat, zip_fixture(entries))
    binary._download(plat, HOST, TAG)
    root = cache / TAG / plat
    library = root / "chromix/Framework/Library"
    assert library.is_symlink()
    assert os.readlink(library) == "Versions/Current/Library"
    assert library.read_bytes() == b"library"
    assert (root / "chromix/Framework/chrome").read_bytes() == b"chrome fixture"


@pytest.mark.parametrize("plat", ["linux-x64", "linux-arm64", "win-x64", "win-arm64"])
@pytest.mark.parametrize("failure", ["http", "network", "stream", "checksum", "corrupt", "missing-launcher", "missing-binary", "directory-binary"])
def test_failure_cleanup_preserves_old_cache_and_retry(cache, monkeypatch, plat, failure):
    chrome_name = binary._binary_path(plat, Path(".")).as_posix()
    root = cache / TAG / plat
    root.mkdir(parents=True)
    marker = root / "old-cache"
    marker.write_text("keep")
    entries = bundle(plat)
    if failure == "missing-launcher":
        entries = [entry for entry in entries if entry[0] != binary._ASSETS[plat][2]]
    if failure in ("missing-binary", "directory-binary"):
        entries = [entry for entry in entries if entry[0] != chrome_name]
    if failure == "directory-binary":
        entries.append(file(f"{chrome_name}/", b"", stat.S_IFDIR | 0o755))
    mock_release(monkeypatch, plat, b"not a ZIP" if failure == "corrupt" else zip_fixture(entries), failure)
    with pytest.raises((OSError, RuntimeError, zipfile.BadZipFile)):
        binary._download(plat, HOST, TAG)
    assert marker.read_text() == "keep"
    assert list((cache / TAG).iterdir()) == [root]
    mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    binary._download(plat, HOST, TAG)
    assert not marker.exists()
    assert binary._bundle_complete(plat, root)


@pytest.mark.parametrize("plat", ["linux-x64", "linux-arm64", "win-x64", "win-arm64"])
@pytest.mark.parametrize("missing", ["launcher", "binary", "directory", "external-link"])
def test_public_api_rejects_incomplete_cache(cache, monkeypatch, plat, missing):
    if missing == "external-link" and os.name == "nt":
        pytest.skip("POSIX symlink fixture")
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    tag = "v155.0.8059.39" if plat.startswith("linux-") else "v154.0.8037.97" if plat.startswith("win-") else TAG
    root = cache / tag / plat
    launcher = root / binary._ASSETS[plat][2]
    chrome = binary._binary_path(plat, root)
    chrome.parent.mkdir(parents=True)
    if missing != "launcher":
        launcher.write_bytes(b"old")
    if missing == "directory":
        chrome.mkdir()
    elif missing == "external-link":
        outside = cache / "outside"
        outside.write_bytes(b"outside")
        chrome.symlink_to(outside)
    elif missing != "binary":
        chrome.write_bytes(b"old")
    info = api.binary_info(release_channel="stable")
    assert not info["installed"] and info["path"] is None
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    assert api.ensure_binary(release_channel="stable") == chrome
    assert len(urls) == 2


@pytest.mark.parametrize("channel", ["stable", "latest"])
@pytest.mark.parametrize("machine,plat,other", [
    ("AMD64", "win-x64", "win-arm64"), ("ARM64", "win-arm64", "win-x64"),
])
@pytest.mark.parametrize("failure", ["", "http"])
def test_windows_cache_isolation_without_architecture_fallback(cache, monkeypatch, machine, plat, other, failure, channel):
    monkeypatch.setattr(binary.platform, "system", lambda: "Windows")
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    tag = "v154.0.8037.97"
    other_root = cache / tag / other
    (other_root / "chromix").mkdir(parents=True)
    (other_root / "chromix/chromix.cmd").write_text("other launcher")
    (other_root / "chromix/chrome.exe").write_text(other)
    assert binary._bundle_complete(other, other_root)
    assert not api.binary_info(release_channel=channel)["installed"]
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)), failure)
    if failure:
        with pytest.raises(urllib.error.HTTPError):
            api.ensure_binary(release_channel=channel)
        info = api.binary_info(release_channel=channel)
        assert not info["installed"] and info["path"] is None
        assert urls == [f"{HOST}/chromix-{plat}.zip"]
        assert list((cache / tag).iterdir()) == [other_root]
    else:
        assert api.ensure_binary(release_channel=channel) == cache / tag / plat / "chromix/chrome.exe"
        assert urls == [f"{HOST}/chromix-{plat}.zip", f"{HOST}/SHA256SUMS"]
        assert {root.name for root in (cache / tag).iterdir()} == {plat, other}
    assert (other_root / "chromix/chrome.exe").read_text() == other
    assert binary._bundle_complete(other, other_root)


@pytest.mark.parametrize("machine,plat", [("AMD64", "win-x64"), ("ARM64", "win-arm64")])
@pytest.mark.parametrize("location", ["explicit", "installed", "cached"])
def test_windows_launch_uses_selected_executable_without_local_cdm(cache, monkeypatch, machine, plat, location):
    monkeypatch.setattr(binary.platform, "system", lambda: "Windows")
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    monkeypatch.setenv("CLOAKBROWSER_WIDEVINE", "1")
    monkeypatch.delenv("CLOAKBROWSER_WIDEVINE_CDM", raising=False)
    chrome_root = cache / "installed-chrome"
    cdm = {"explicit": cache / "explicit-cdm",
           "installed": chrome_root / "152.0.0.0" / "WidevineCdm",
           "cached": cache / "widevine" / "WidevineCdm"}[location]
    (cdm / "_platform_specific/win_x64").mkdir(parents=True)
    (cdm / "manifest.json").write_text("{}")
    (cdm / "_platform_specific/win_x64/widevinecdm.dll").write_bytes(b"x64 CDM")
    if location == "explicit":
        monkeypatch.setenv("CLOAKBROWSER_WIDEVINE_CDM", str(cdm))
    mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    chrome, args, _, _ = api._prepare(
        True, None, [], False, None, None, False, None, False, release_channel="stable")
    tag = "v154.0.8037.97"
    assert chrome == cache / tag / plat / "chromix/chrome.exe"
    flags = [arg for arg in args if arg.startswith("--uxr-widevine-cdm=")]
    assert flags == []


UNSAFE_ENTRIES = {
    "traversal": [file("chromix/../../outside", b"changed")],
    "absolute": [file("/chromix/outside")],
    "backslash": [file("chromix/..\\outside")],
    "drive": [file("C:/outside")],
    "ads": [file("chromix/chrome:stream")],
    "reserved": [file("chromix/NUL")],
    "trailing": [file("chromix/chrome.")],
    "duplicate": [file("chromix/chrome")],
    "case-alias": [file("chromix/CHROME")],
    "special": [file("chromix/device", b"", stat.S_IFCHR | 0o644)],
    "symlink-write": [link("chromix/link", "../.."), file("chromix/link/outside", b"changed")],
    "symlink-case-write": [link("chromix/Link", "../.."), file("chromix/link/outside", b"changed")],
    "symlink-escape": [link("chromix/link", "../../../outside")],
    "symlink-absolute": [link("chromix/link", "/outside")],
    "symlink-drive": [link("chromix/link", "C:\\outside")],
    "symlink-dangling": [link("chromix/link", "absent")],
    "symlink-cycle": [link("chromix/a", "b"), link("chromix/b", "a")],
    "symlink-chain": [link("chromix/a", "b"), link("chromix/b", "../../../outside")],
    "symlink-root": [link("chromix", "../../outside")],
}


@pytest.mark.parametrize("name,entries", UNSAFE_ENTRIES.items(), ids=UNSAFE_ENTRIES)
@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_unsafe_zip_cannot_escape_or_publish(cache, monkeypatch, name, entries):
    outside = cache / "outside"
    outside.write_text("untouched")
    mock_release(monkeypatch, "linux-x64", zip_fixture(
        entries if name == "symlink-root" else bundle("linux-x64") + entries))
    with pytest.raises((ValueError, OSError)):
        binary._download("linux-x64", HOST, TAG)
    assert outside.read_text() == "untouched"
    assert list((cache / TAG).iterdir()) == []


def test_missing_manifest_keeps_optional_verification(cache, monkeypatch):
    mock_release(monkeypatch, "linux-x64", zip_fixture(bundle("linux-x64")), "manifest")
    binary._download("linux-x64", HOST, TAG)
    assert binary._bundle_complete("linux-x64", cache / TAG / "linux-x64")


@pytest.mark.parametrize("system,machine,plat", PLATFORMS)
@pytest.mark.parametrize("channel", [None, "stable", "latest"])
def test_platform_channels_info_install_and_cli(cache, monkeypatch, capsys, system, machine, plat, channel):
    from chromix.__main__ import main

    monkeypatch.setattr(binary.platform, "system", lambda: system)
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    if channel:
        monkeypatch.setenv("CLOAKBROWSER_RELEASE_CHANNEL", channel)
    expected = ("155.0.8059.39" if plat and plat.startswith("linux-") else "154.0.8037.97" if plat and plat.startswith("win-") else
                "152.0.7977.75" if channel == "latest" else "151.0.7922.173")
    monkeypatch.delenv("CHROMIX_DOWNLOAD_HOST")
    calls = []

    def download(platform, host, tag):
        calls.append((platform, host, tag))
        assert platform == plat
        assert tag == f"v{expected}"
        assert host == f"https://github.com/xiaozhou26/Chromix/releases/download/{tag}"
        root = cache / tag / plat
        chrome = binary._binary_path(plat, root)
        chrome.parent.mkdir(parents=True, exist_ok=True)
        chrome.write_text("fixture")
        (root / binary._ASSETS[plat][2]).write_text("launcher")

    monkeypatch.setattr(api, "_download", download)
    assert api.binary_info()["version"] == expected
    assert api.binary_info()["channel"] == (channel or "stable")
    chrome = api.ensure_binary()
    assert api.binary_info()["path"] == str(chrome)
    monkeypatch.setattr(sys, "argv", ["chromix", "info"])
    main()
    assert json.loads(capsys.readouterr().out)["version"] == expected
    monkeypatch.setattr(sys, "argv", ["chromix", "install"])
    main()
    assert capsys.readouterr().out.strip() == str(chrome)
    assert len(calls) == 2


@pytest.mark.parametrize("system,machine,plat", PLATFORMS)
@pytest.mark.parametrize("channel,old_tag", [
    ("stable", "v151.0.7922.173"), ("latest", "v152.0.7977.75"),
])
def test_platform_channel_promotion_preserves_old_caches(cache, monkeypatch, system, machine, plat, channel, old_tag):
    monkeypatch.setattr(binary.platform, "system", lambda: system)
    monkeypatch.setattr(binary.platform, "machine", lambda: machine)
    old_root = cache / old_tag / plat
    old_chrome = binary._binary_path(plat, old_root)
    old_chrome.parent.mkdir(parents=True)
    old_chrome.write_text("old browser")
    (old_root / binary._ASSETS[plat][2]).write_text("old launcher")
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    promoted = plat in ("linux-x64", "linux-arm64", "win-x64", "win-arm64")
    assert api.binary_info(release_channel=channel)["installed"] == (not promoted)
    selected_tag = "v155.0.8059.39" if plat.startswith("linux-") else "v154.0.8037.97" if promoted else old_tag
    assert api.ensure_binary(release_channel=channel) == binary._binary_path(plat, cache / selected_tag / plat)
    assert len(urls) == (2 if promoted else 0)
    assert api.ensure_binary(browser_version=old_tag) == old_chrome
    assert old_chrome.read_text() == "old browser"
    assert len(urls) == (2 if promoted else 0)


@pytest.mark.parametrize("plat", ["win-x64", "win-arm64"])
@pytest.mark.parametrize("channel,old_tag", [
    ("stable", "v151.0.7922.173"), ("latest", "v152.0.7977.75"),
])
def test_windows_promotion_failure_never_uses_old_channel_cache(cache, monkeypatch, plat, channel, old_tag):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    old_root = cache / old_tag / plat
    old_chrome = binary._binary_path(plat, old_root)
    old_chrome.parent.mkdir(parents=True)
    old_chrome.write_text("old browser")
    (old_root / "chromix/chromix.cmd").write_text("old launcher")
    urls = mock_release(monkeypatch, plat, b"", "http")
    with pytest.raises(urllib.error.HTTPError):
        api.ensure_binary(release_channel=channel)
    info = api.binary_info(release_channel=channel)
    assert info["version"] == "154.0.8037.97"
    assert not info["installed"] and info["path"] is None
    assert api.ensure_binary(browser_version=old_tag) == old_chrome
    assert old_chrome.read_text() == "old browser"
    assert urls == [f"{HOST}/chromix-{plat}.zip"]
    assert list((cache / "v154.0.8037.97").iterdir()) == []


@pytest.mark.parametrize("system,machine,plat", PLATFORMS)
@pytest.mark.parametrize("version,expected", [
    ("151", "151.0.7922.173"), ("152", "152.0.7977.75"),
    ("151.0.7922.100", "151.0.7922.100"), ("v154.0.8037.97", "154.0.8037.97"),
    ("154.0.8037.97", "154.0.8037.97"),
])
def test_explicit_versions_do_not_follow_new_default(cache, monkeypatch, system, machine, plat, version, expected):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    info = api.binary_info(browser_version=version)
    assert info["version"] == expected
    if "." in version:
        assert info["channel"] is None
    monkeypatch.setenv("CLOAKBROWSER_VERSION", version)
    assert api.binary_info() == info
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    chrome = api.ensure_binary(browser_version=version)
    assert chrome == binary._binary_path(plat, cache / f"v{expected}" / plat)
    assert api.binary_info()["path"] == str(chrome)
    assert len(urls) == 2


@pytest.mark.parametrize("plat", binary._ASSETS)
def test_major_and_invalid_selectors(cache, monkeypatch, plat):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    if plat in ("linux-x64", "linux-arm64", "win-x64", "win-arm64"):
        assert api.binary_info(browser_version="154")["version"] == "154.0.8037.97"
    else:
        with pytest.raises(ValueError, match="Unsupported browser version"):
            api.binary_info(browser_version="154")
    for version in ("155", "v155"):
        if plat.startswith("linux-"):
            assert api.binary_info(browser_version=version)["version"] == "155.0.8059.39"
        else:
            with pytest.raises(ValueError, match="Unsupported browser version"):
                api.binary_info(browser_version=version)
            with pytest.raises(ValueError, match="Unsupported browser version"):
                api.ensure_binary(browser_version=version)
    for version in ("15", "999", "152.0", "../154.0.8037.97"):
        with pytest.raises(ValueError, match="Unsupported browser version"):
            api.binary_info(browser_version=version)
        with pytest.raises(ValueError):
            api.ensure_binary(browser_version=version)
    with pytest.raises(ValueError, match="Unknown release channel"):
        api.binary_info(release_channel="unknown")
    monkeypatch.setenv("CLOAKBROWSER_RELEASE_CHANNEL", "latest")
    assert api.binary_info(browser_version="151")["channel"] == "latest"
    assert api.binary_info(release_channel="stable")["channel"] == "stable"
    monkeypatch.delenv("CLOAKBROWSER_RELEASE_CHANNEL")
    monkeypatch.setenv("CLOAKBROWSER_VERSION", "151")
    assert api.binary_info(browser_version="152")["version"] == "152.0.7977.75"


@pytest.mark.parametrize("plat", [*binary._ASSETS, None])
def test_local_override_bypasses_resolution_but_info_stays_metadata(cache, monkeypatch, plat):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    local = cache / "local-chrome"
    local.write_text("local")
    monkeypatch.setenv("CLOAKBROWSER_BINARY_PATH", str(local))
    assert api.ensure_binary(browser_version="invalid", release_channel="invalid") == local
    info = api.binary_info()
    assert info["version"] == ("155.0.8059.39" if plat and plat.startswith("linux-") else "154.0.8037.97" if plat and plat.startswith("win-") else "151.0.7922.173")
    assert not info["installed"] and info["path"] is None
    local.unlink()
    with pytest.raises(FileNotFoundError):
        api.ensure_binary()


@pytest.mark.parametrize("plat", binary._ASSETS)
@pytest.mark.parametrize("case", ["linux-only", "matching", "older", "offline", "prerelease", "draft", "no-assets", "invalid"])
def test_update_requires_newer_platform_asset(cache, monkeypatch, plat, case):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    monkeypatch.setenv("CLOAKBROWSER_RELEASE_CHANNEL", "latest")
    current = "155.0.8059.39" if plat and plat.startswith("linux-") else "154.0.8037.97" if plat and plat.startswith("win-") else "152.0.7977.75"
    candidate = "156.0.1.2" if case == "matching" else "154.0.8037.97"
    if case == "older":
        candidate = "151.0.7922.173"
    if case == "invalid":
        candidate = "not-a-version"
    release = {"tag_name": f"v{candidate}", "prerelease": case == "prerelease", "draft": case == "draft",
               "assets": [{"name": binary._ASSETS["linux-x64" if case == "linux-only" else plat][0]}]}

    if case == "no-assets":
        release["assets"] = []

    def urlopen(request, timeout):
        assert request.full_url.endswith("/releases/latest")
        if case == "offline":
            raise OSError("offline")
        return io.BytesIO(json.dumps(release).encode())

    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    update = api.check_for_update()
    assert update == {"current_version": current, "latest_version": candidate if case == "matching" else current,
                      "update_available": case == "matching"}
    assert api.check_for_update(browser_version="155.0.1.2", release_channel="stable")["current_version"] == (
        "155.0.8059.39" if plat and plat.startswith("linux-") else "154.0.8037.97" if plat and plat.startswith("win-") else "151.0.7922.173")


def test_explicit_missing_release_never_falls_back(cache, monkeypatch):
    monkeypatch.setattr(api, "resolve_platform", lambda: "win-arm64")
    urls = mock_release(monkeypatch, "win-arm64", b"", "http")
    with pytest.raises(urllib.error.HTTPError):
        api.ensure_binary(browser_version="154.0.8037.97")
    assert urls == [f"{HOST}/chromix-win-arm64.zip"]
    assert api.binary_info(browser_version="154.0.8037.97")["version"] == "154.0.8037.97"


def test_update_uses_exact_environment_and_per_call_versions(cache, monkeypatch):
    monkeypatch.setattr(api, "resolve_platform", lambda: "linux-x64")
    monkeypatch.setenv("CLOAKBROWSER_VERSION", "151.0.7922.100")
    release = {"tag_name": "v154.0.8037.97", "assets": [{"name": "chromix-linux-x64.zip"}]}
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(release).encode()))
    assert api.check_for_update() == {"current_version": "151.0.7922.100", "latest_version": "154.0.8037.97",
                                     "update_available": True}
    assert api.check_for_update(browser_version="155.0.1.2") == {
        "current_version": "155.0.1.2", "latest_version": "155.0.1.2", "update_available": False}
    monkeypatch.setattr(api, "resolve_platform", lambda: None)
    assert not api.check_for_update()["update_available"]


@pytest.mark.parametrize("plat", ["linux-x64", "linux-arm64"])
@pytest.mark.parametrize("version", ["155", "v155", "155.0.8059.39", "v155.0.8059.39", "154", "v154"])
def test_linux_release_selectors_install_exact_tag(cache, monkeypatch, plat, version):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    expected = "154.0.8037.97" if version.lstrip("v") == "154" else "155.0.8059.39"
    monkeypatch.setenv("CLOAKBROWSER_VERSION", version)
    info = api.binary_info()
    assert info["version"] == expected
    assert info["channel"] == ("stable" if version in ("155", "v155") else None)
    urls = mock_release(monkeypatch, plat, zip_fixture(bundle(plat)))
    chrome = api.ensure_binary()
    assert chrome == binary._binary_path(plat, cache / f"v{expected}" / plat)
    assert api.binary_info()["installed"]
    assert urls == [f"{HOST}/chromix-{plat}.zip", f"{HOST}/SHA256SUMS"]


@pytest.mark.parametrize("plat", binary._ASSETS)
@pytest.mark.parametrize("version", ["155.0.8059.39", "v155.0.8059.39"])
def test_missing_exact_155_never_uses_available_154(cache, monkeypatch, plat, version):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    old_root = cache / "v154.0.8037.97" / plat
    old_chrome = binary._binary_path(plat, old_root)
    old_chrome.parent.mkdir(parents=True)
    old_chrome.write_text("old browser")
    (old_root / binary._ASSETS[plat][2]).write_text("old launcher")
    urls = mock_release(monkeypatch, plat, b"", "http")
    with pytest.raises(urllib.error.HTTPError):
        api.ensure_binary(browser_version=version)
    info = api.binary_info(browser_version=version)
    assert info["version"] == "155.0.8059.39"
    assert info["channel"] is None
    assert not info["installed"] and info["path"] is None
    assert api.ensure_binary(browser_version="154.0.8037.97") == old_chrome
    assert old_chrome.read_text() == "old browser"
    assert urls == [f"{HOST}/chromix-{plat}.zip"]


@pytest.mark.parametrize("plat", binary._ASSETS)
def test_linux_only_155_release_is_not_an_update_for_other_platforms(cache, monkeypatch, plat):
    monkeypatch.setattr(api, "resolve_platform", lambda: plat)
    release = {"tag_name": "v155.0.8059.39", "assets": [
        {"name": "chromix-linux-x64.zip"}, {"name": "chromix-linux-arm64.zip"}]}
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(release).encode()))
    expected = "155.0.8059.39" if plat.startswith("linux-") else "154.0.8037.97"
    assert api.check_for_update(browser_version="154.0.8037.97") == {
        "current_version": "154.0.8037.97", "latest_version": expected,
        "update_available": plat.startswith("linux-")}
