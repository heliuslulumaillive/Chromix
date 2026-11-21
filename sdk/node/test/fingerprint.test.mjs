import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeFingerprintArgs } from "../_fingerprint.js";
import { lookupProxy, networkArgs, resolveWebrtcArgs } from "../_network.js";
import { buildArgs } from "../index.js";

for (const mode of ["off", "false", "0", "disable", "disabled", "OFF", "FALSE"]) {
  test(`off canonicalization: ${mode}`, () => {
    const args = ["--fingerprint-platform=windows", `--fingerprint=${mode}`, "--fingerprint-timezone=UTC"];
    assert.deepEqual(normalizeFingerprintArgs(args), ["--fingerprint=off", "--fingerprint-timezone=UTC"]);
    assert.equal(args[0], "--fingerprint-platform=windows");
  });
}

for (const prefix of ['--fingerprint-', '--uxr-']) {
  for (const flag of ['timer-resolution=7', 'timer-resolution=0', 'max-touch-points=16',
    'gpu-backend=native', 'audio-seed=18446744073709551615',
    'pointer=fine', 'hover=none', 'color-scheme=dark', 'forced-colors=active',
    'preferred-contrast=less', 'codec-h264=disabled',
    'codec-av1=native', 'codec-vp9=supported,smooth,power-efficient']) {
    test(`backend option syntax ${prefix}${flag}`, () => {
      assert.deepEqual(normalizeFingerprintArgs([prefix + flag]), [prefix + flag]);
    });
  }
  for (const flag of ['timer-resolution=+1', 'timer-resolution=1.5', 'timer-resolution=1001',
    'max-touch-points=17', 'audio-seed=0', 'audio-seed=18446744073709551616', 'audio-render=noise',
    'gpu-backend=software', 'hdr=high', 'color-scheme=auto', 'codec-h264=smooth',
    'codec-h264=supported,', 'codec-vp8= supported', 'codec-vp9=supported,unknown',
    'keyboard-layout=us', 'font-policy=restricted']) {
    test(`invalid backend value ${prefix}${flag}`, () => assert.throws(() => normalizeFingerprintArgs([prefix + flag])));
  }
}

test('effective aliases, mixed pointer and synthetic keyboard', () => {
  const valid = ['--fingerprint-pointer=none', '--uxr-pointer=fine', '--fingerprint-max-touch-points=5'];
  assert.deepEqual(normalizeFingerprintArgs(valid), valid);
  for (const [pointer, touch] of [['none', '1'], ['coarse', '0']])
    assert.throws(() => normalizeFingerprintArgs([`--uxr-pointer=${pointer}`, `--fingerprint-max-touch-points=${touch}`]), /inconsistent/);
  assert.throws(()=>normalizeFingerprintArgs(['--uxr-synthetic-device-tests=true', '--fingerprint-keyboard-layout=us']), /Retired/);
});

test('retired browser surface modes and list options are rejected', () => {
  for(const prefix of ['--fingerprint-','--uxr-']){
    for(const key of ['font-policy=native','font-whitelist=Arial','voices-mode=native','offset-mode=cloak',
                      'canvas-mode=chromix','webgl-mode=cloak','audio-render=isolated'])
      assert.throws(()=>normalizeFingerprintArgs([prefix+key]));
    for(const key of ['canvas-mode=shardx','webgl-mode=native','webgpu-identity-mode=cloak'])
      assert.deepEqual(normalizeFingerprintArgs([prefix+key]),[prefix+key]);
  }
});

for (const prefix of ['--fingerprint-', '--uxr-']) {
  for (const mode of ['native', 'seeded']) {
    test(`pixel noise preserves backend and fonts: ${prefix}${mode}`, () => {
      const flag = `${prefix}pixel-noise=${mode}`;
      assert.deepEqual(normalizeFingerprintArgs([flag]), [flag]);
      for (const backend of ['native', 'compatibility']) {
        const args = ['--fingerprint=42', flag, `${prefix}gpu-backend=${backend}`];
        assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:args}), args);
        const synthetic = [...args, '--uxr-synthetic-device-tests=true'];
        assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:synthetic}), synthetic);
      }
      const native = [`${prefix}pixel-noise=native`];
      assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:native}), native);
    });
  }
  for (const suffix of ['', '=', '=auto', '=noise', '=true', '=false', '=0', '=1',
    '=NATIVE', '=Seeded', '= seeded', '=seeded ', '=native,seeded']) {
    for (const disabled of [[], ['--fingerprint=off'], ['--fingerprint-noise=false']]) {
      test(`invalid pixel noise even when disabled: ${prefix}${suffix} ${disabled}`, () => {
        assert.throws(() => buildArgs({extraArgs:[...disabled, `${prefix}pixel-noise${suffix}`]}),
          /pixel-noise requires one of/);
      });
    }
  }
  test(`pixel noise seed dependency after defaults: ${prefix}`, () => {
    const mode = [`${prefix}pixel-noise=seeded`];
    assert.deepEqual(normalizeFingerprintArgs(mode), mode);
    assert.throws(() => buildArgs({stealthArgs:false, extraArgs:mode}), /seeded pixel-noise requires/);
    const result = buildArgs({extraArgs:mode});
    assert.ok(result.includes(mode[0]));
    assert.ok(result.some((a) => a.startsWith('--fingerprint=') && BigInt(a.split('=')[1]) > 0n));
    assert.ok(!result.some((a) => a.includes('gpu-backend') || a.includes('synthetic-device-tests')));
    for (const seed of ['1', '42', '18446744073709551615']) {
      for (const flag of ['--fingerprint=', '--uxr-canvas-seed=']) {
        const args = [...mode, flag + seed];
        assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:args}), args);
      }
    }
    // A bare public fingerprint switch retains the browser's existing seed generation.
    for (const flag of ['--fingerprint', '--fingerprint=']) {
      const args = [...mode, flag];
      assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:args}), args);
    }
    assert.throws(() => buildArgs({stealthArgs:false, extraArgs:[...mode, '--fingerprint-audio-seed=42']}),
      /seeded pixel-noise requires/);
    for (const seed of ['', '0', '-1', '+1', '1.5', ' 42', '42 ', '42\n', '42\r', 'abc', '18446744073709551616']) {
      assert.throws(() => buildArgs({extraArgs:[...mode, `--uxr-canvas-seed=${seed}`]}),
        /nonzero uint64 canvas seed/);
    }
  });
}

for (const [name, seeds, valid] of [
  ["missing", [], false],
  ["internal-fingerprint-only", ["--uxr-fingerprint-seed=42"], false],
  ["canvas-only", ["--uxr-canvas-seed=42"], true],
  ["canvas-zero", ["--uxr-canvas-seed=0"], false],
  ["public-seed", ["--fingerprint=42"], true],
  ["public-with-other-internal", ["--fingerprint=42", "--uxr-fingerprint-seed=99"], true],
  ["public-ignores-invalid-internal", ["--fingerprint=42", "--uxr-fingerprint-seed=invalid"], true],
  ["public-ignores-zero-internal", ["--fingerprint=42", "--uxr-fingerprint-seed=0"], true],
  ["public-ignores-empty-internal", ["--fingerprint=42", "--uxr-fingerprint-seed="], true],
  ["public-with-canvas", ["--fingerprint=42", "--uxr-canvas-seed=99"], true],
  ["public-with-zero-canvas", ["--fingerprint=42", "--uxr-canvas-seed=0"], false],
  ["public-with-empty-canvas", ["--fingerprint=42", "--uxr-canvas-seed="], false],
  ["public-with-bare-canvas", ["--fingerprint=42", "--uxr-canvas-seed"], false],
  ["bare-public-random", ["--fingerprint"], true],
  ["empty-public-random", ["--fingerprint="], true],
  ["empty-public-inherits-internal", ["--fingerprint=", "--uxr-fingerprint-seed=42"], true],
  ["empty-public-empty-internal-random", ["--fingerprint=", "--uxr-fingerprint-seed="], true],
  ["empty-public-zero-internal", ["--fingerprint=", "--uxr-fingerprint-seed=0"], false],
  ["empty-public-invalid-internal", ["--fingerprint=", "--uxr-fingerprint-seed=invalid"], false],
  ["empty-public-internal-off", ["--fingerprint=", "--uxr-fingerprint-seed=off"], false],
  ["empty-public-explicit-canvas", ["--fingerprint=", "--uxr-canvas-seed=42"], true],
  ["empty-public-zero-canvas", ["--fingerprint=", "--uxr-canvas-seed=0"], false],
  ["empty-public-empty-canvas", ["--fingerprint=", "--uxr-canvas-seed="], false],
  ["canvas-wins-over-invalid-internal", ["--uxr-canvas-seed=42", "--uxr-fingerprint-seed=invalid"], true],
  ["canvas-wins-over-inherited-invalid-internal", ["--fingerprint=", "--uxr-fingerprint-seed=invalid", "--uxr-canvas-seed=42"], true],
  ["canvas-wins-over-internal-off", ["--fingerprint=", "--uxr-fingerprint-seed=off", "--uxr-canvas-seed=42"], true],
  ["canvas-invalid-despite-valid-internal", ["--fingerprint=", "--uxr-fingerprint-seed=42", "--uxr-canvas-seed=0"], false],
  ["canvas-last-valid", ["--uxr-canvas-seed=0", "--uxr-canvas-seed=42"], true],
  ["canvas-last-invalid", ["--uxr-canvas-seed=42", "--uxr-canvas-seed=0"], false],
  ["inherited-internal-last-valid", ["--fingerprint=", "--uxr-fingerprint-seed=0", "--uxr-fingerprint-seed=42"], true],
  ["inherited-internal-last-invalid", ["--fingerprint=", "--uxr-fingerprint-seed=42", "--uxr-fingerprint-seed=0"], false],
  ["off-without-seed", ["--fingerprint=off"], true],
  ["off-with-invalid-canvas", ["--fingerprint=off", "--uxr-canvas-seed=0"], true],
  ["off-with-empty-canvas", ["--fingerprint=off", "--uxr-canvas-seed="], true],
  ["noise-false-without-seed", ["--fingerprint-noise=false"], true],
  ["noise-false-with-invalid-canvas", ["--fingerprint-noise=false", "--uxr-canvas-seed=0"], true],
  ["noise-false-with-invalid-inherited-seed", ["--fingerprint=", "--uxr-fingerprint-seed=invalid", "--fingerprint-noise=false"], true],
  ["internal-noise-off-with-invalid-canvas", ["--uxr-disable-fingerprint-noise", "--uxr-canvas-seed=0"], true],
]) {
  for (const prefix of ['--fingerprint-', '--uxr-']) {
    test(`pixel noise canvas seed matrix: ${prefix} ${name}`, () => {
      const args = [`${prefix}pixel-noise=seeded`, ...seeds];
      assert.deepEqual(normalizeFingerprintArgs(args), args);
      if (valid) {
        const expected = [...new Map(args.map((arg) => [arg.split('=', 1)[0], arg])).values()];
        assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:args}), expected);
      } else {
        assert.throws(() => buildArgs({stealthArgs:false, extraArgs:args}),
          /seeded pixel-noise requires.*canvas seed/);
      }
    });
  }
}

for (const seed of ['0', '-1', '+1', '1.5', ' 42', '42 ', '42\n', '42\r', 'abc', '18446744073709551616']) {
  test(`pixel noise validates only effective canvas seed: ${JSON.stringify(seed)}`, () => {
    const mode = ['--fingerprint-pixel-noise=seeded', `--uxr-fingerprint-seed=${seed}`];
    const result = buildArgs({extraArgs:mode});
    assert.ok(mode.every((arg) => result.includes(arg)));
    assert.throws(() => buildArgs({stealthArgs:false, extraArgs:[...mode, '--fingerprint=']}),
      /nonzero uint64 canvas seed/);
  });
}

for (const disabled of ['--fingerprint=off', '--fingerprint=FALSE', '--fingerprint-noise=false',
  '--fingerprint-noise=disabled', '--uxr-disable-fingerprint-noise']) {
  for (const stealthArgs of [false, true]) {
    test(`pixel noise disable priority preserves seed policy: ${disabled} ${stealthArgs}`, () => {
      const mode = ['--fingerprint-pixel-noise=seeded', '--uxr-pixel-noise=seeded'];
      for (const args of [[disabled, ...mode], [...mode, disabled]]) {
        const result = buildArgs({stealthArgs, extraArgs:args});
        assert.ok(mode.every((flag) => result.includes(flag)));
        if (disabled.startsWith('--fingerprint=')) {
          assert.ok(result.includes('--fingerprint=off'));
          assert.ok(!result.some((a) => a.startsWith('--fingerprint=') && a !== '--fingerprint=off'));
        } else if (!stealthArgs) {
          assert.ok(!result.some((a) => a.startsWith('--fingerprint=')));
        }
        assert.ok(!result.some((a) => a.includes('gpu-backend') || a.includes('synthetic-device-tests')));
      }
    });
  }
}

test('pixel noise alias priority and default passthrough', () => {
  assert.ok(!buildArgs().some((a) => a.includes('pixel-noise')));
  assert.deepEqual(buildArgs({stealthArgs:false}), []);
  for (const reverse of [false, true]) {
    const native = ['--fingerprint-pixel-noise=seeded', '--uxr-pixel-noise=native'];
    const seeded = ['--fingerprint-pixel-noise=native', '--uxr-pixel-noise=seeded'];
    if (reverse) { native.reverse(); seeded.reverse(); }
    assert.deepEqual(buildArgs({stealthArgs:false, extraArgs:native}), native);
    assert.throws(() => buildArgs({stealthArgs:false, extraArgs:seeded}), /seeded pixel-noise requires/);
  }
  assert.deepEqual(buildArgs({stealthArgs:false,
    extraArgs:['--fingerprint-pixel-noise=seeded', '--fingerprint-pixel-noise=native']}),
  ['--fingerprint-pixel-noise=native']);
  assert.throws(() => buildArgs({stealthArgs:false,
    extraArgs:['--fingerprint-pixel-noise=native', '--fingerprint-pixel-noise=seeded']}), /seeded pixel-noise requires/);
  assert.throws(() => buildArgs({stealthArgs:false, extraArgs:['--fingerprint-pixel-noise=seeded',
    '--fingerprint-noise=false', '--fingerprint-noise=true']}), /seeded pixel-noise requires/);
});

test('retired isolated audio is rejected even when a valid seed is present', () => {
  for(const seed of [[],['--fingerprint=42'],['--fingerprint-audio-seed=42']])
    assert.throws(()=>buildArgs({stealthArgs:false,extraArgs:['--fingerprint-audio-render=isolated',...seed]}));
  assert.ok(!buildArgs({headless:false}).includes('--ignore-gpu-blocklist'));
  assert.ok(buildArgs({extraArgs:['--ignore-gpu-blocklist']}).includes('--ignore-gpu-blocklist'));
});

for (const flag of ['--proxy-pac-url=https://example.test/proxy.pac', '--proxy-auto-detect']) {
  test(`proxy policy for ${flag}`, () => {
    assert.equal(networkArgs([flag]).at(-1), '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
    assert.deepEqual(networkArgs([flag, '--no-proxy-server']), [flag, '--no-proxy-server']);
  });
}

for (const brand of ["Chrome", "Edge", "Opera", "Vivaldi"]) {
  test(`brand canonicalization: ${brand}`, () => {
    assert.equal(normalizeFingerprintArgs([`--fingerprint-brand=${brand.toLowerCase()}`])[0], `--fingerprint-brand=${brand}`);
  });
}

for (const flag of ["--fingerprint=18446744073709551616", "--fingerprint=-1", "--fingerprint=abc",
  "--fingerprint-brand=Unknown", "--fingerprint-brand-version=1.2.3.4.5", "--fingerprint-brand-version=x\nInjected:1",
  "--fingerprint-hardware-concurrency=129", "--fingerprint-device-memory=nan", "--fingerprint-device-memory=inf",
  "--fingerprint-device-memory=0", "--fingerprint-screen-width=-1", "--fingerprint-taskbar-height=-1",
  "--fingerprint-storage-quota=8796093022208", "--fingerprint-noise=maybe"]) {
  test(`invalid public switch: ${flag}`, () => assert.throws(() => normalizeFingerprintArgs([flag])));
}

for (const ip of ["198.51.100.7", "2001:db8::7"]) {
  test(`explicit IP has no lookup: ${ip}`, async () => {
    const args = [`--fingerprint-webrtc-ip=${ip}`];
    assert.deepEqual(await resolveWebrtcArgs(args, null, { lookup: () => assert.fail("lookup attempted") }), args);
  });
}

for (const ip of ["", "example.com", "1.2.3.4:80", "[::1]", "fe80::1%eth0", "127.1", "1.2.3.999", "1.2.3.4\r\n"]) {
  test(`invalid IP: ${JSON.stringify(ip)}`, () => assert.throws(() => networkArgs([`--fingerprint-webrtc-ip=${ip}`]), /IPv4\/IPv6/));
}

test("auto resolves once through the explicit proxy and keeps region flags", async () => {
  const requests = [];
  const args = ["--fingerprint=42", "--fingerprint-webrtc-ip=auto", "--fingerprint-locale=fr-FR"];
  const proxy = { server: "http://proxy.example:8080", username: "u@", password: "p:", bypass: "*" };
  const result = await resolveWebrtcArgs(args, proxy, { lookup: async (route) => {
    requests.push(route); return { timezone: "Asia/Tokyo", locale: "ja-JP", exitIp: "203.0.113.9" };
  } });
  assert.deepEqual(requests, ["http://u%40:p%3A@proxy.example:8080"]);
  assert.ok(result.includes("--fingerprint-webrtc-ip=203.0.113.9"));
  assert.ok(result.includes("--fingerprint-locale=fr-FR"));
  assert.ok(!result.some((arg) => arg.includes("timezone")));
  assert.equal(args[1], "--fingerprint-webrtc-ip=auto");
});

test("GeoIP result is reused, and explicit native alias wins", async () => {
  const options = { geoip: true, exitIp: "203.0.113.9", lookup: () => assert.fail("duplicate lookup") };
  assert.ok((await resolveWebrtcArgs(["--fingerprint=42"], null, options)).includes("--fingerprint-webrtc-ip=203.0.113.9"));
  const args = ["--fingerprint-webrtc-ip=auto", "--uxr-webrtc-ip=198.51.100.8"];
  assert.deepEqual(await resolveWebrtcArgs(args, null, options), args);
});

test("off does not resolve auto", async () => {
  const result = await resolveWebrtcArgs(["--fingerprint=false", "--fingerprint-webrtc-ip=auto"], null,
    { lookup: () => assert.fail("lookup in off mode") });
  assert.ok(result.includes("--fingerprint=off"));
});

test("raw proxy flag is honored without direct fallback", async () => {
  const args = ["--proxy-server=http://proxy.example:8080", "--fingerprint-webrtc-ip=auto"];
  const routes = [];
  const result = await resolveWebrtcArgs(args, null, { lookup: async (route) => {
    routes.push(route); return { exitIp: "203.0.113.5" };
  } });
  assert.deepEqual(routes, ["http://proxy.example:8080"]);
  assert.ok(result.includes("--fingerprint-webrtc-ip=203.0.113.5"));
  await assert.rejects(resolveWebrtcArgs(args, null, { lookup: () => { throw new Error("lookup failed"); } }), /lookup failed/);
});

for (const arg of ['--proxy-server=', '--proxy-server=http://one:80,direct://',
  '--proxy-server=http=one:80;https=two:80', '--proxy-server=http://u:p@one:80',
  '--proxy-pac-url=https://proxy.example/proxy.pac', '--proxy-auto-detect']) {
  for (const proxy of [undefined, 'http://one:80']) {
    test(`ambiguous auto route rejects before lookup: ${arg}, ${proxy}`, async () => {
      await assert.rejects(resolveWebrtcArgs([arg, '--fingerprint-webrtc-ip=auto'], proxy,
        { lookup: () => assert.fail('ambiguous route reached lookup') }), /GeoIP\/auto/);
    });
  }
}

test('proxy route conflicts and no-proxy precedence', () => {
  const proxy = { server: 'http://one:8080', username: 'u', password: 'p' };
  assert.deepEqual(lookupProxy(['--proxy-server=http://one:8080'], proxy), proxy);
  assert.throws(() => lookupProxy(['--proxy-server=http://two:8080'], proxy), /conflicts/);
  assert.throws(() => lookupProxy(['--no-proxy-server'], proxy), /conflicts/);
  const args = ['--proxy-server=http://one:8080', '--no-proxy-server'];
  assert.equal(lookupProxy(args), undefined);
  assert.deepEqual(networkArgs(args, proxy), args);
  assert.equal(networkArgs(args.slice(0, 1)).at(-1), '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
  assert.equal(networkArgs([args[0], '--force-webrtc-ip-handling-policy=default']).at(-1),
    '--force-webrtc-ip-handling-policy=default');
});

for (const [raw, configured] of [
  ['http://one:80', 'http://ONE'],
  ['https://one:443', 'https://one'],
  ['socks5://one:1080', 'socks5://one'],
  ['http://[2001:db8:0:0:0:0:0:1]:80', 'http://[2001:db8::1]'],
]) {
  test(`equivalent proxy endpoints keep credentials: ${raw}`, () => {
    const proxy = { server: configured, username: 'u', password: 'p' };
    assert.deepEqual(lookupProxy([`--proxy-server=${raw}`], proxy), proxy);
  });
}
