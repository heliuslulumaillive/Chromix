"""Public flag compatibility and pre-launch IP resolution."""
import pytest

from chromix._fingerprint import normalize_fingerprint_args
from chromix._network import lookup_proxy, network_args, resolve_webrtc_args


@pytest.mark.parametrize('mode', ['off', 'false', '0', 'disable', 'disabled', 'OFF', 'FALSE'])
def test_off_aliases_strip_inherited_platform(mode):
    args = ['--fingerprint-platform=windows', '--fingerprint=' + mode, '--fingerprint-timezone=UTC']
    assert normalize_fingerprint_args(args) == ['--fingerprint=off', '--fingerprint-timezone=UTC']
    assert args[0] == '--fingerprint-platform=windows'


@pytest.mark.parametrize('brand', ['Chrome', 'Edge', 'Opera', 'Vivaldi'])
def test_brands_and_versions(brand):
    args = ['--fingerprint-brand=' + brand.lower(), '--fingerprint-brand-version=152.0.7977.82']
    assert normalize_fingerprint_args(args)[0] == '--fingerprint-brand=' + brand


@pytest.mark.parametrize('flag', [
    '--fingerprint=18446744073709551616', '--fingerprint=-1', '--fingerprint=abc',
    '--fingerprint-brand=Unknown', '--fingerprint-brand-version=1.2.3.4.5',
    '--fingerprint-brand-version=x\nInjected:1', '--fingerprint-hardware-concurrency=129',
    '--fingerprint-device-memory=nan', '--fingerprint-device-memory=inf', '--fingerprint-device-memory=0',
    '--fingerprint-screen-width=-1', '--fingerprint-taskbar-height=-1',
    '--fingerprint-storage-quota=8796093022208', '--fingerprint-noise=maybe',
])
def test_invalid_switches_fail_before_browser_start(flag):
    with pytest.raises(ValueError):
        normalize_fingerprint_args([flag])


@pytest.mark.parametrize('ip', ['198.51.100.7', '2001:db8::7'])
def test_explicit_ip_does_not_call_resolver(ip):
    def forbidden(*args):
        pytest.fail('explicit IP must not trigger an HTTP request')
    args = ['--fingerprint-webrtc-ip=' + ip]
    assert resolve_webrtc_args(args, lookup=forbidden) == args


@pytest.mark.parametrize('ip', ['', 'example.com', '1.2.3.4:80', '[::1]', 'fe80::1%eth0', '127.1', '1.2.3.999', '1.2.3.4\r\n'])
def test_invalid_ip_values(ip):
    with pytest.raises(ValueError, match='IPv4/IPv6'):
        network_args(['--fingerprint-webrtc-ip=' + ip])


def test_auto_resolves_once_using_proxy_credentials_without_changing_region():
    requests = []
    def lookup(proxy):
        requests.append(proxy)
        return 'Asia/Tokyo', 'ja-JP', '203.0.113.9'
    args = ['--fingerprint=42', '--fingerprint-webrtc-ip=auto', '--fingerprint-locale=fr-FR']
    proxy = {'server':'http://proxy.example:8080', 'username':'u@', 'password':'p:', 'bypass':'*'}
    result = resolve_webrtc_args(args, proxy, lookup=lookup)
    assert requests == ['http://u%40:p%3A@proxy.example:8080']
    assert '--fingerprint-webrtc-ip=203.0.113.9' in result
    assert '--fingerprint-locale=fr-FR' in result
    assert not any('timezone' in arg for arg in result)
    assert args[1] == '--fingerprint-webrtc-ip=auto'


def test_geoip_reuses_lookup_and_explicit_native_alias_wins():
    def forbidden(*args):
        pytest.fail('the GeoIP lookup must be reused')
    result = resolve_webrtc_args(['--fingerprint=42'], geoip=True, exit_ip='203.0.113.9', lookup=forbidden)
    assert '--fingerprint-webrtc-ip=203.0.113.9' in result
    args = ['--fingerprint-webrtc-ip=auto', '--uxr-webrtc-ip=198.51.100.8']
    assert resolve_webrtc_args(args, geoip=True, exit_ip='203.0.113.9', lookup=forbidden) == args


def test_off_auto_does_not_make_a_request():
    def forbidden(*args):
        pytest.fail('off must not resolve a fingerprint-only address')
    result = resolve_webrtc_args(['--fingerprint=false', '--fingerprint-webrtc-ip=auto'], lookup=forbidden)
    assert '--fingerprint=off' in result


def test_raw_proxy_flag_is_used_for_auto():
    requests = []
    def lookup(proxy):
        requests.append(proxy)
        return 'UTC', 'en-US', '203.0.113.5'
    result = resolve_webrtc_args(['--proxy-server=http://proxy.example:8080', '--fingerprint-webrtc-ip=auto'], lookup=lookup)
    assert requests == ['http://proxy.example:8080']
    assert '--fingerprint-webrtc-ip=203.0.113.5' in result


def test_auto_failure_has_no_fallback():
    def fail(proxy):
        raise ValueError('lookup failed')
    with pytest.raises(ValueError, match='lookup failed'):
        resolve_webrtc_args(['--fingerprint-webrtc-ip=auto'], 'http://proxy.example:8080', lookup=fail)


@pytest.mark.parametrize('arg', [
    '--proxy-server=', '--proxy-server=http://one:80,direct://',
    '--proxy-server=http=one:80;https=two:80', '--proxy-server=http://u:p@one:80',
    '--proxy-pac-url=https://proxy.example/proxy.pac', '--proxy-auto-detect',
])
@pytest.mark.parametrize('proxy', [None, 'http://one:80'])
def test_ambiguous_auto_routes_fail_before_lookup(arg, proxy):
    def forbidden(*args):
        pytest.fail('ambiguous route must be rejected before lookup')
    with pytest.raises(ValueError, match='GeoIP/auto'):
        resolve_webrtc_args([arg, '--fingerprint-webrtc-ip=auto'], proxy, lookup=forbidden)


def test_proxy_route_conflicts_and_no_proxy_precedence():
    proxy = {'server': 'http://one:8080', 'username': 'u', 'password': 'p'}
    assert lookup_proxy(['--proxy-server=http://one:8080'], proxy) == proxy
    with pytest.raises(ValueError, match='conflicts'):
        lookup_proxy(['--proxy-server=http://two:8080'], proxy)
    with pytest.raises(ValueError, match='conflicts'):
        lookup_proxy(['--no-proxy-server'], proxy)
    args = ['--proxy-server=http://one:8080', '--no-proxy-server']
    assert lookup_proxy(args) is None
    assert network_args(args, proxy) == args
    assert network_args(args[:1])[-1] == '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'
    assert network_args(args[:1] + ['--force-webrtc-ip-handling-policy=default'])[-1].endswith('=default')


@pytest.mark.parametrize('raw, configured', [
    ('http://one:80', 'http://ONE'),
    ('https://one:443', 'https://one'),
    ('socks5://one:1080', 'socks5://one'),
    ('http://[2001:db8:0:0:0:0:0:1]:80', 'http://[2001:db8::1]'),
])
def test_equivalent_proxy_endpoints_keep_high_level_credentials(raw, configured):
    proxy = {'server': configured, 'username': 'u', 'password': 'p'}
    assert lookup_proxy(['--proxy-server=' + raw], proxy) == proxy


@pytest.mark.parametrize('prefix', ['--fingerprint-', '--uxr-'])
@pytest.mark.parametrize('flag', ['timer-resolution=7', 'timer-resolution=0', 'max-touch-points=16',
    'gpu-backend=native',  'audio-seed=18446744073709551615',
    'pointer=fine', 'hover=none', 'color-scheme=dark', 'forced-colors=active',
    'preferred-contrast=less',  'codec-h264=disabled',
    'codec-av1=native', 'codec-vp9=supported,smooth,power-efficient'])
def test_backend_options_have_matching_public_and_native_syntax(prefix, flag):
    args = [prefix + flag]
    assert normalize_fingerprint_args(args) == args


@pytest.mark.parametrize('flag', ['timer-resolution=+1', 'timer-resolution=1.5', 'timer-resolution=1001',
    'max-touch-points=17', 'audio-seed=0', 'audio-seed=18446744073709551616', 'audio-render=noise',
    'gpu-backend=software', 'hdr=high', 'color-scheme=auto', 'codec-h264=smooth',
    'codec-h264=supported,', 'codec-vp8= supported', 'codec-vp9=supported,unknown',
    'keyboard-layout=us', 'font-policy=restricted'])
def test_backend_invalid_values_fail_before_launch(flag):
    for prefix in ('--fingerprint-', '--uxr-'):
        with pytest.raises(ValueError):
            normalize_fingerprint_args([prefix + flag])


def test_effective_aliases_mixed_input_and_synthetic_keyboard():
    valid = ['--fingerprint-pointer=none', '--uxr-pointer=fine', '--fingerprint-max-touch-points=5']
    assert normalize_fingerprint_args(valid) == valid
    for pair in [('none', '1'), ('coarse', '0')]:
        with pytest.raises(ValueError, match='inconsistent'):
            normalize_fingerprint_args(['--uxr-pointer=' + pair[0], '--fingerprint-max-touch-points=' + pair[1]])
    with pytest.raises(ValueError, match='Retired'):
        normalize_fingerprint_args(['--uxr-synthetic-device-tests=true', '--fingerprint-keyboard-layout=us'])


@pytest.mark.parametrize("prefix", ["--fingerprint-", "--uxr-"])
def test_retired_surface_options_are_rejected(prefix):
    for key in ["font-policy=native","font-whitelist=Arial","voices-mode=native","offset-mode=cloak",
                "canvas-mode=chromix","webgl-mode=cloak","audio-render=isolated"]:
        with pytest.raises(ValueError):
            normalize_fingerprint_args([prefix+key])
    for key in ["canvas-mode=shardx","webgl-mode=native","webgpu-identity-mode=cloak"]:
        assert normalize_fingerprint_args([prefix+key]) == [prefix+key]


@pytest.mark.parametrize('prefix', ['--fingerprint-', '--uxr-'])
@pytest.mark.parametrize('mode', ['native', 'seeded'])
def test_pixel_noise_modes_preserve_backend_and_fonts(prefix, mode):
    from chromix.api import build_args
    flag = prefix + 'pixel-noise=' + mode
    assert normalize_fingerprint_args([flag]) == [flag]
    for backend in ('native', 'compatibility'):
        args = ['--fingerprint=42', flag, prefix + 'gpu-backend=' + backend]
        assert build_args(False, args) == args
        synthetic = args + ['--uxr-synthetic-device-tests=true']
        assert build_args(False, synthetic) == synthetic
    assert build_args(False, [prefix + 'pixel-noise=native']) == [prefix + 'pixel-noise=native']


@pytest.mark.parametrize('prefix', ['--fingerprint-', '--uxr-'])
@pytest.mark.parametrize('suffix', ['', '=', '=auto', '=noise', '=true', '=false', '=0', '=1',
                                     '=NATIVE', '=Seeded', '= seeded', '=seeded ', '=native,seeded'])
@pytest.mark.parametrize('disabled', [[], ['--fingerprint=off'], ['--fingerprint-noise=false']])
def test_pixel_noise_rejects_invalid_values_even_when_disabled(prefix, suffix, disabled):
    from chromix.api import build_args
    args = disabled + [prefix + 'pixel-noise' + suffix]
    with pytest.raises(ValueError, match='pixel-noise requires one of'):
        build_args(True, args)


@pytest.mark.parametrize('prefix', ['--fingerprint-', '--uxr-'])
def test_pixel_noise_seed_dependency_after_defaults(prefix):
    from chromix.api import build_args
    mode = [prefix + 'pixel-noise=seeded']
    assert normalize_fingerprint_args(mode) == mode
    with pytest.raises(ValueError, match='seeded pixel-noise requires'):
        build_args(False, mode)
    result = build_args(True, mode)
    assert mode[0] in result
    assert any(a.startswith('--fingerprint=') and int(a.split('=')[1]) > 0 for a in result)
    assert not any('gpu-backend' in a or 'synthetic-device-tests' in a for a in result)
    for seed in ('1', '42', '18446744073709551615'):
        for flag in ('--fingerprint=', '--uxr-canvas-seed='):
            args = mode + [flag + seed]
            assert build_args(False, args) == args
    # A bare public fingerprint switch retains the browser's existing seed generation.
    for flag in ('--fingerprint', '--fingerprint='):
        assert build_args(False, mode + [flag]) == mode + [flag]
    with pytest.raises(ValueError, match='seeded pixel-noise requires'):
        build_args(False, mode + ['--fingerprint-audio-seed=42'])
    for seed in ('', '0', '-1', '+1', '1.5', ' 42', '42 ', '42\n', '42\r', 'abc', '18446744073709551616'):
        with pytest.raises(ValueError, match='nonzero uint64 canvas seed'):
            build_args(True, mode + ['--uxr-canvas-seed=' + seed])


@pytest.mark.parametrize('prefix', ['--fingerprint-', '--uxr-'])
@pytest.mark.parametrize('name, seeds, valid', [
    ('missing', [], False),
    ('internal-fingerprint-only', ['--uxr-fingerprint-seed=42'], False),
    ('canvas-only', ['--uxr-canvas-seed=42'], True),
    ('canvas-zero', ['--uxr-canvas-seed=0'], False),
    ('public-seed', ['--fingerprint=42'], True),
    ('public-with-other-internal', ['--fingerprint=42', '--uxr-fingerprint-seed=99'], True),
    ('public-ignores-invalid-internal', ['--fingerprint=42', '--uxr-fingerprint-seed=invalid'], True),
    ('public-ignores-zero-internal', ['--fingerprint=42', '--uxr-fingerprint-seed=0'], True),
    ('public-ignores-empty-internal', ['--fingerprint=42', '--uxr-fingerprint-seed='], True),
    ('public-with-canvas', ['--fingerprint=42', '--uxr-canvas-seed=99'], True),
    ('public-with-zero-canvas', ['--fingerprint=42', '--uxr-canvas-seed=0'], False),
    ('public-with-empty-canvas', ['--fingerprint=42', '--uxr-canvas-seed='], False),
    ('public-with-bare-canvas', ['--fingerprint=42', '--uxr-canvas-seed'], False),
    ('bare-public-random', ['--fingerprint'], True),
    ('empty-public-random', ['--fingerprint='], True),
    ('empty-public-inherits-internal', ['--fingerprint=', '--uxr-fingerprint-seed=42'], True),
    ('empty-public-empty-internal-random', ['--fingerprint=', '--uxr-fingerprint-seed='], True),
    ('empty-public-zero-internal', ['--fingerprint=', '--uxr-fingerprint-seed=0'], False),
    ('empty-public-invalid-internal', ['--fingerprint=', '--uxr-fingerprint-seed=invalid'], False),
    ('empty-public-internal-off', ['--fingerprint=', '--uxr-fingerprint-seed=off'], False),
    ('empty-public-explicit-canvas', ['--fingerprint=', '--uxr-canvas-seed=42'], True),
    ('empty-public-zero-canvas', ['--fingerprint=', '--uxr-canvas-seed=0'], False),
    ('empty-public-empty-canvas', ['--fingerprint=', '--uxr-canvas-seed='], False),
    ('canvas-wins-over-invalid-internal', ['--uxr-canvas-seed=42', '--uxr-fingerprint-seed=invalid'], True),
    ('canvas-wins-over-inherited-invalid-internal', ['--fingerprint=', '--uxr-fingerprint-seed=invalid', '--uxr-canvas-seed=42'], True),
    ('canvas-wins-over-internal-off', ['--fingerprint=', '--uxr-fingerprint-seed=off', '--uxr-canvas-seed=42'], True),
    ('canvas-invalid-despite-valid-internal', ['--fingerprint=', '--uxr-fingerprint-seed=42', '--uxr-canvas-seed=0'], False),
    ('canvas-last-valid', ['--uxr-canvas-seed=0', '--uxr-canvas-seed=42'], True),
    ('canvas-last-invalid', ['--uxr-canvas-seed=42', '--uxr-canvas-seed=0'], False),
    ('inherited-internal-last-valid', ['--fingerprint=', '--uxr-fingerprint-seed=0', '--uxr-fingerprint-seed=42'], True),
    ('inherited-internal-last-invalid', ['--fingerprint=', '--uxr-fingerprint-seed=42', '--uxr-fingerprint-seed=0'], False),
    ('off-without-seed', ['--fingerprint=off'], True),
    ('off-with-invalid-canvas', ['--fingerprint=off', '--uxr-canvas-seed=0'], True),
    ('off-with-empty-canvas', ['--fingerprint=off', '--uxr-canvas-seed='], True),
    ('noise-false-without-seed', ['--fingerprint-noise=false'], True),
    ('noise-false-with-invalid-canvas', ['--fingerprint-noise=false', '--uxr-canvas-seed=0'], True),
    ('noise-false-with-invalid-inherited-seed', ['--fingerprint=', '--uxr-fingerprint-seed=invalid', '--fingerprint-noise=false'], True),
    ('internal-noise-off-with-invalid-canvas', ['--uxr-disable-fingerprint-noise', '--uxr-canvas-seed=0'], True),
], ids=lambda value: value if isinstance(value, str) else None)
def test_pixel_noise_canvas_seed_matrix(prefix, name, seeds, valid):
    from chromix.api import build_args
    args = [prefix + 'pixel-noise=seeded'] + seeds
    assert normalize_fingerprint_args(args) == args
    if valid:
        expected = list({arg.partition('=')[0]: arg for arg in args}.values())
        assert build_args(False, args) == expected
    else:
        with pytest.raises(ValueError, match='seeded pixel-noise requires.*canvas seed'):
            build_args(False, args)


@pytest.mark.parametrize('seed', ['0', '-1', '+1', '1.5', ' 42', '42 ', '42\n', '42\r', 'abc', '18446744073709551616'])
def test_pixel_noise_validates_only_effective_canvas_seed(seed):
    from chromix.api import build_args
    mode = ['--fingerprint-pixel-noise=seeded', '--uxr-fingerprint-seed=' + seed]
    assert all(arg in build_args(True, mode) for arg in mode)
    with pytest.raises(ValueError, match='nonzero uint64 canvas seed'):
        build_args(False, mode + ['--fingerprint='])


@pytest.mark.parametrize('disabled', ['--fingerprint=off', '--fingerprint=FALSE',
    '--fingerprint-noise=false', '--fingerprint-noise=disabled', '--uxr-disable-fingerprint-noise'])
@pytest.mark.parametrize('stealth', [False, True])
def test_pixel_noise_disable_priority_does_not_reenable_seed(disabled, stealth):
    from chromix.api import build_args
    mode = ['--fingerprint-pixel-noise=seeded', '--uxr-pixel-noise=seeded']
    for args in ([disabled] + mode, mode + [disabled]):
        result = build_args(stealth, args)
        assert all(flag in result for flag in mode)
        if disabled.startswith('--fingerprint='):
            assert '--fingerprint=off' in result
            assert not any(a.startswith('--fingerprint=') and a != '--fingerprint=off' for a in result)
        elif not stealth:
            assert not any(a.startswith('--fingerprint=') for a in result)
        assert not any('gpu-backend' in a or 'synthetic-device-tests' in a for a in result)


def test_pixel_noise_alias_priority_and_default_passthrough():
    from chromix.api import build_args
    assert not any('pixel-noise' in a for a in build_args(True, []))
    assert build_args(False, []) == []
    for reverse in (False, True):
        native = ['--fingerprint-pixel-noise=seeded', '--uxr-pixel-noise=native']
        seeded = ['--fingerprint-pixel-noise=native', '--uxr-pixel-noise=seeded']
        if reverse:
            native.reverse()
            seeded.reverse()
        assert build_args(False, native) == native
        with pytest.raises(ValueError, match='seeded pixel-noise requires'):
            build_args(False, seeded)
    assert build_args(False, ['--fingerprint-pixel-noise=seeded', '--fingerprint-pixel-noise=native']) == [
        '--fingerprint-pixel-noise=native']
    with pytest.raises(ValueError, match='seeded pixel-noise requires'):
        build_args(False, ['--fingerprint-pixel-noise=native', '--fingerprint-pixel-noise=seeded'])
    with pytest.raises(ValueError, match='seeded pixel-noise requires'):
        build_args(False, ['--fingerprint-pixel-noise=seeded', '--fingerprint-noise=false', '--fingerprint-noise=true'])


def test_retired_isolated_audio_is_rejected_even_with_seed():
    from chromix.api import build_args
    for seed in ([], ["--fingerprint=42"], ["--fingerprint-audio-seed=42"]):
        with pytest.raises(ValueError, match="Retired"):
            build_args(False, ["--fingerprint-audio-render=isolated", *seed])
    assert '--ignore-gpu-blocklist' not in build_args(True, [], headless=False)
    assert '--ignore-gpu-blocklist' in build_args(True, ['--ignore-gpu-blocklist'])


@pytest.mark.parametrize('flag', ['--proxy-pac-url=https://example.test/proxy.pac', '--proxy-auto-detect'])
def test_pac_and_auto_detect_apply_native_webrtc_restriction(flag):
    assert network_args([flag])[-1] == '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'
    assert network_args([flag, '--no-proxy-server']) == [flag, '--no-proxy-server']
