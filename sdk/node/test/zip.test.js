// Offline download/extraction integration tests; fixtures are real ZIP bytes.
import { after, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync, existsSync, statSync, lstatSync, readlinkSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { Readable } from "node:stream";
import { deflateRawSync } from "node:zlib";

const hostPlatform = process.platform;
const cache = mkdtempSync(join(tmpdir(), "chromix-zip-"));
const originalCache = process.env.CHROMIX_CACHE_DIR;
process.env.CHROMIX_CACHE_DIR = cache;
const binary = await import("../_binary.js");
const api = await import("../index.js");
if (originalCache === undefined) delete process.env.CHROMIX_CACHE_DIR;
else process.env.CHROMIX_CACHE_DIR = originalCache;
after(() => rmSync(cache, { recursive: true, force: true }));
beforeEach(() => {
  for (const name of readdirSync(cache)) rmSync(join(cache, name), { recursive: true, force: true });
});
const host = "https://fixtures.invalid/release";
const tag = binary.CHANNELS.stable.tag;
const options = { releaseChannel: "stable" };
const file = (name, data = "fixture", mode = 0o100644) => ({ name, data, mode });
const link = (name, target) => file(name, target, 0o120777);

function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function zipFixture(entries) {
  const local = [], central = [];
  let offset = 0;
  for (const { name, data, mode } of entries) {
    const filename = Buffer.from(name), bytes = Buffer.from(data), compressed = deflateRawSync(bytes);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(8, 8);
    header.writeUInt32LE(crc32(bytes), 14);
    header.writeUInt32LE(compressed.length, 18);
    header.writeUInt32LE(bytes.length, 22);
    header.writeUInt16LE(filename.length, 26);
    local.push(header, filename, compressed);
    const record = Buffer.alloc(46);
    record.writeUInt32LE(0x02014b50, 0);
    record.writeUInt16LE(0x0314, 4);
    header.copy(record, 6, 4, 28);
    record.writeUInt32LE((mode * 65536) >>> 0, 38);
    record.writeUInt32LE(offset, 42);
    central.push(record, filename);
    offset += header.length + filename.length + compressed.length;
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}

function bundle(plat) {
  return [file("chromix/", "", 0o40755), file(binary.ASSETS[plat].launcher),
    file(relative(".", binary.binaryPath(plat, ".")).split("\\").join("/"), "chrome fixture"),
    file("chromix/helper", "helper", 0o104755), file("chromix/resources.pak", "resources")];
}

function mockRelease(t, plat, bytes, failure = "") {
  const urls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    urls.push(url);
    if (url.endsWith("/SHA256SUMS")) {
      const hash = failure === "checksum" ? "0".repeat(64) : createHash("sha256").update(bytes).digest("hex");
      return new Response(`${hash.toUpperCase()} *${binary.ASSETS[plat].asset}\n`, { status: failure === "manifest" ? 404 : 200 });
    }
    assert.equal(url, `${host}/${binary.ASSETS[plat].asset}`);
    if (failure === "http") return new Response("missing", { status: 404 });
    if (failure === "network") throw new Error("network down");
    if (failure === "stream") return { ok: true, body: Readable.from((async function* () {
      yield bytes.subarray(0, 10);
      throw new Error("stream interrupted");
    })()) };
    return new Response(bytes);
  });
  return urls;
}

function mockPlatform(t, platform, arch) {
  const descriptors = Object.fromEntries(["platform", "arch"].map((key) => [key, Object.getOwnPropertyDescriptor(process, key)]));
  Object.defineProperty(process, "platform", { value: platform, configurable: true });
  Object.defineProperty(process, "arch", { value: arch, configurable: true });
  t.after(() => Object.defineProperties(process, descriptors));
  for (const [key, value] of [["CHROMIX_DOWNLOAD_HOST", host], ["CLOAKBROWSER_BINARY_PATH", ""],
    ["CLOAKBROWSER_VERSION", ""], ["CLOAKBROWSER_RELEASE_CHANNEL", ""]]) {
    const original = process.env[key];
    process.env[key] = value;
    t.after(() => {
      if (original === undefined) delete process.env[key];
      else process.env[key] = original;
    });
  }
}

for (const [platform, arch, plat] of [["linux", "x64", "linux-x64"], ["linux", "arm64", "linux-arm64"],
  ["win32", "x64", "win-x64"], ["win32", "arm64", "win-arm64"],
  ["darwin", "x64", "mac-x64"], ["darwin", "arm64", "mac-arm64"]]) {
  test(`ZIP download, public API and cache: ${plat}`, async (t) => {
    mockPlatform(t, platform, arch);
    const tag = plat.startsWith("linux-") ? "v155.0.8059.39" : plat.startsWith("win-") ? "v154.0.8037.97" : "v151.0.7922.173";
    const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
    assert.equal(binary.resolvePlatform(), plat);
    assert.equal(api.binaryInfo(options).installed, false);
    const root = join(cache, tag, plat), chrome = binary.binaryPath(plat, root);
    assert.equal(await api.ensureBinary(options), chrome);
    assert.equal(readFileSync(chrome, "utf8"), "chrome fixture");
    assert.equal(api.binaryInfo(options).platform, plat);
    assert.equal(api.binaryInfo(options).path, chrome);
    assert.equal(api.binaryInfo(options).installed, true);
    assert.equal(await binary.ensureNative(plat, host, tag), join(root, binary.ASSETS[plat].launcher));
    assert.equal(await api.ensureBinary(options), chrome);
    assert.deepEqual(urls, [`${host}/${binary.ASSETS[plat].asset}`, `${host}/SHA256SUMS`]);
    assert.deepEqual(readdirSync(join(cache, tag)), [plat]);
    assert.equal(existsSync(join(root, binary.ASSETS[plat].asset)), false);
    if (plat.startsWith("mac-")) assert.ok(chrome.endsWith(join("Chromium.app", "Contents", "MacOS", "Chromium")));
    if (hostPlatform !== "win32" && platform !== "win32") {
      for (const path of [chrome, join(root, binary.ASSETS[plat].launcher), join(root, "chromix/helper")])
        assert.equal(statSync(path).mode & 0o7777, 0o755);
      assert.equal(statSync(join(root, "chromix/resources.pak")).mode & 0o777, 0o644);
    }
  });
}

for (const [arch, plat] of [["x64", "win-x64"], ["arm64", "win-arm64"]])
for (const directories of [false, true]) {
  test(`Windows backslash ZIP download: ${plat}, directories=${directories}`, async (t) => {
    mockPlatform(t, "win32", arch);
    let entries = [file("chromix\\chromix.cmd", "launcher", 0), file("chromix\\chrome.exe", "chrome fixture", 0),
      file("chromix\\locales/en-US.pak", "locale", 0)];
    if (directories) entries = [file("chromix\\", "", 0), file("chromix\\locales\\", "", 0),
      file("chromix\\empty\\", "", 0), ...entries];
    const urls = mockRelease(t, plat, zipFixture(entries));
    const chrome = await api.ensureBinary(), root = dirname(dirname(chrome));
    assert.equal(readFileSync(chrome, "utf8"), "chrome fixture");
    assert.equal(readFileSync(join(root, "chromix", "locales", "en-US.pak"), "utf8"), "locale");
    if (directories) assert.equal(statSync(join(root, "chromix", "empty")).isDirectory(), true);
    assert.equal(api.binaryInfo().installed, true);
    assert.equal(await api.ensureBinary(), chrome);
    assert.equal(urls.length, 2);
  });
}

for (const plat of ["linux-x64", "linux-arm64", "mac-x64", "mac-arm64"]) {
  test(`POSIX target rejects backslash ZIP even on Windows host: ${plat}`, async (t) => {
    mockPlatform(t, "win32", "x64");
    mockRelease(t, plat, zipFixture([...bundle(plat), file("chromix\\helper2")]));
    await assert.rejects(binary.ensureNative(plat, host, tag), /invalid characters in fileName/);
    assert.deepEqual(readdirSync(join(cache, tag)), []);
  });
}

const windowsUnsafeEntries = {
  mixedDuplicate: [file("chromix\\chrome.exe")],
  mixedCaseCollision: [file("chromix\\CHROME.EXE")],
  directoryCollision: [file("chromix\\", "", 0)],
  traversal: [file("chromix\\..\\..\\outside")],
  mixedTraversal: [file("chromix/locales\\../outside")],
  dot: [file("chromix\\.\\helper")],
  emptyComponent: [file("chromix\\\\helper")],
  unc: [file("\\\\server\\chromix\\helper")],
  absolute: [file("\\chromix\\helper")],
  drive: [file("C:\\chromix\\helper")],
  driveRelative: [file("C:chromix\\helper")],
  ads: [file("chromix\\chrome.exe:stream")],
  device: [file("chromix\\NUL.txt")],
  trailingDot: [file("chromix\\helper.")],
  trailingSpace: [file("chromix\\helper ")],
  linkEscape: [link("chromix\\link", "../../outside")],
  linkBackslashEscape: [link("chromix\\link", "..\\..\\outside")],
  linkUnc: [link("chromix\\link", "\\\\server\\outside")],
  linkDrive: [link("chromix\\link", "C:\\outside")],
  linkTraversal: [link("chromix\\link", "../.."), file("chromix\\link/outside")],
  linkCaseTraversal: [link("chromix\\Link", "../.."), file("chromix/link\\outside")],
};
for (const plat of ["win-x64", "win-arm64"])
for (const [name, entries] of Object.entries(windowsUnsafeEntries)) {
  test(`Windows backslash ZIP rejects unsafe paths: ${plat} ${name}`, async (t) => {
    writeFileSync(join(cache, "outside"), "untouched");
    mockRelease(t, plat, zipFixture([...bundle(plat), ...entries]));
    await assert.rejects(binary.ensureNative(plat, host, tag));
    assert.equal(readFileSync(join(cache, "outside"), "utf8"), "untouched");
    assert.deepEqual(readdirSync(join(cache, tag)), []);
  });
}

test("ZIP preserves framework links, chains and internal parent-relative targets", { skip: process.platform === "win32" }, async (t) => {
  const plat = "mac-arm64", root = join(cache, tag, plat);
  const entries = [...bundle(plat), file("chromix/Framework/Versions/A/Library", "library", 0o100755),
    link("chromix/Framework/Library", "Versions/Current/Library"),
    link("chromix/Framework/Versions/Current", "A"), link("chromix/Framework/chrome", "../Chromium.app/Contents/MacOS/Chromium")];
  mockRelease(t, plat, zipFixture(entries));
  await binary.ensureNative(plat, host, tag);
  const library = join(root, "chromix/Framework/Library");
  assert.ok(lstatSync(library).isSymbolicLink());
  assert.equal(readlinkSync(library), "Versions/Current/Library");
  assert.equal(readFileSync(library, "utf8"), "library");
  assert.equal(readFileSync(join(root, "chromix/Framework/chrome"), "utf8"), "chrome fixture");
});

for (const plat of ["linux-x64", "linux-arm64", "win-x64", "win-arm64"])
for (const failure of ["http", "network", "stream", "checksum", "corrupt", "missing-launcher", "missing-binary", "directory-binary"]) {
  test(`ZIP ${plat} ${failure} cleans staging, keeps old cache and permits retry`, async (t) => {
    const root = join(cache, tag, plat), marker = join(root, "old-cache");
    const chromeName = relative(".", binary.binaryPath(plat, ".")).split("\\").join("/");
    mkdirSync(root, { recursive: true });
    writeFileSync(marker, "keep");
    let entries = bundle(plat);
    if (failure === "missing-launcher") entries = entries.filter((entry) => entry.name !== binary.ASSETS[plat].launcher);
    if (failure === "missing-binary") entries = entries.filter((entry) => entry.name !== chromeName);
    if (failure === "directory-binary") entries = [...entries.filter((entry) => entry.name !== chromeName), file(`${chromeName}/`, "", 0o40755)];
    mockRelease(t, plat, failure === "corrupt" ? Buffer.from("not a ZIP") : zipFixture(entries), failure);
    await assert.rejects(binary.ensureNative(plat, host, tag));
    assert.equal(readFileSync(marker, "utf8"), "keep");
    assert.deepEqual(readdirSync(join(cache, tag)), [plat]);
    mockRelease(t, plat, zipFixture(bundle(plat)));
    await binary.ensureNative(plat, host, tag);
    assert.equal(existsSync(marker), false);
    assert.equal(binary.bundleComplete(plat, root), true);
  });
}

for (const [platform, arch, plat] of [["linux", "x64", "linux-x64"], ["linux", "arm64", "linux-arm64"],
  ["win32", "x64", "win-x64"], ["win32", "arm64", "win-arm64"]])
for (const missing of ["launcher", "binary", "directory", "external-link"]) {
  test(`public API rejects incomplete ${plat} cache: ${missing}`, { skip: missing === "external-link" && process.platform === "win32" }, async (t) => {
    mockPlatform(t, platform, arch);
    const tag = plat.startsWith("linux-") ? "v155.0.8059.39" : plat.startsWith("win-") ? "v154.0.8037.97" : "v151.0.7922.173";
    const root = join(cache, tag, plat);
    const launcher = join(root, binary.ASSETS[plat].launcher), chrome = binary.binaryPath(plat, root);
    mkdirSync(dirname(chrome), { recursive: true });
    if (missing !== "launcher") writeFileSync(launcher, "old");
    if (missing === "directory") mkdirSync(chrome);
    else if (missing === "external-link") {
      writeFileSync(join(cache, "outside"), "outside");
      symlinkSync(join(cache, "outside"), chrome);
    } else if (missing !== "binary") writeFileSync(chrome, "old");
    assert.equal(api.binaryInfo(options).installed, false);
    assert.equal(api.binaryInfo(options).path, null);
    const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
    assert.equal(await api.ensureBinary(options), chrome);
    assert.equal(urls.length, 2);
  });
}

for (const channel of ["stable", "latest"])
for (const [arch, plat, other] of [["x64", "win-x64", "win-arm64"], ["arm64", "win-arm64", "win-x64"]])
for (const failure of ["", "http"]) {
  test(`Windows cache isolation without architecture fallback: ${plat} ${channel} ${failure || "success"}`, async (t) => {
    mockPlatform(t, "win32", arch);
    const options = { releaseChannel: channel };
    const tag = "v154.0.8037.97";
    const otherRoot = join(cache, tag, other);
    mkdirSync(join(otherRoot, "chromix"), { recursive: true });
    writeFileSync(join(otherRoot, "chromix", "chromix.cmd"), "other launcher");
    writeFileSync(join(otherRoot, "chromix", "chrome.exe"), other);
    assert.equal(binary.bundleComplete(other, otherRoot), true);
    assert.equal(api.binaryInfo(options).installed, false);
    const urls = mockRelease(t, plat, zipFixture(bundle(plat)), failure);
    if (failure) {
      await assert.rejects(api.ensureBinary(options), /download failed: 404/);
      assert.equal(api.binaryInfo(options).installed, false);
      assert.equal(api.binaryInfo(options).path, null);
      assert.deepEqual(urls, [`${host}/chromix-${plat}.zip`]);
      assert.deepEqual(readdirSync(join(cache, tag)), [other]);
    } else {
      assert.equal(await api.ensureBinary(options), join(cache, tag, plat, "chromix", "chrome.exe"));
      assert.deepEqual(urls, [`${host}/chromix-${plat}.zip`, `${host}/SHA256SUMS`]);
      assert.deepEqual(readdirSync(join(cache, tag)).sort(), [plat, other].sort());
    }
    assert.equal(readFileSync(join(otherRoot, "chromix", "chrome.exe"), "utf8"), other);
    assert.equal(binary.bundleComplete(other, otherRoot), true);
  });
}

for (const [arch, plat] of [["x64", "win-x64"], ["arm64", "win-arm64"]]) {
  test(`Windows launch uses the selected executable without local CDM preload: ${plat}`, async (t) => {
    mockPlatform(t, "win32", arch);
    const cdm = join(cache, "widevine", "WidevineCdm");
    mkdirSync(join(cdm, "_platform_specific", "win_x64"), { recursive: true });
    writeFileSync(join(cdm, "manifest.json"), "{}");
    writeFileSync(join(cdm, "_platform_specific", "win_x64", "widevinecdm.dll"), "x64 CDM");
    for (const [key, value] of [["CLOAKBROWSER_WIDEVINE_CDM", cdm], ["CLOAKBROWSER_WIDEVINE", "1"]]) {
      const original = process.env[key];
      process.env[key] = value;
      t.after(() => {
        if (original === undefined) delete process.env[key];
        else process.env[key] = original;
      });
    }
    mockRelease(t, plat, zipFixture(bundle(plat)));
    const tag = "v154.0.8037.97";
    const launch = await api.buildLaunchOptions({ ...options, stealthArgs: false });
    assert.equal(launch.executablePath, join(cache, tag, plat, "chromix", "chrome.exe"));
    assert.ok(!launch.args.some(arg => arg.startsWith("--uxr-widevine-cdm=")));
  });
}

const unsafeEntries = {
  traversal: [file("chromix/../../outside", "changed")],
  absolute: [file("/chromix/outside")],
  backslash: [file("chromix/..\\outside")],
  drive: [file("C:/outside")],
  ads: [file("chromix/chrome:stream")],
  reserved: [file("chromix/NUL")],
  trailing: [file("chromix/chrome.")],
  duplicate: [file("chromix/chrome")],
  caseAlias: [file("chromix/CHROME")],
  special: [file("chromix/device", "", 0o020644)],
  symlinkWrite: [link("chromix/link", "../.."), file("chromix/link/outside", "changed")],
  symlinkCaseWrite: [link("chromix/Link", "../.."), file("chromix/link/outside", "changed")],
  symlinkEscape: [link("chromix/link", "../../../outside")],
  symlinkAbsolute: [link("chromix/link", "/outside")],
  symlinkDrive: [link("chromix/link", "C:\\outside")],
  symlinkDangling: [link("chromix/link", "absent")],
  symlinkCycle: [link("chromix/a", "b"), link("chromix/b", "a")],
  symlinkChain: [link("chromix/a", "b"), link("chromix/b", "../../../outside")],
  symlinkRoot: [link("chromix", "../../outside")],
};
for (const [name, entries] of Object.entries(unsafeEntries)) {
  test(`ZIP rejects ${name} without escaping or publishing cache`, async (t) => {
    const plat = "linux-x64";
    writeFileSync(join(cache, "outside"), "untouched");
    mockRelease(t, plat, zipFixture(name === "symlinkRoot" ? entries : [...bundle(plat), ...entries]));
    await assert.rejects(binary.ensureNative(plat, host, tag));
    assert.equal(readFileSync(join(cache, "outside"), "utf8"), "untouched");
    assert.deepEqual(readdirSync(join(cache, tag)), []);
  });
}

test("missing SHA256SUMS retains optional-manifest behavior", async (t) => {
  mockRelease(t, "linux-x64", zipFixture(bundle("linux-x64")), "manifest");
  await binary.ensureNative("linux-x64", host, tag);
  assert.equal(binary.bundleComplete("linux-x64", join(cache, tag, "linux-x64")), true);
});

for (const [platform, arch, plat] of [["linux", "x64", "linux-x64"], ["linux", "arm64", "linux-arm64"],
  ["win32", "x64", "win-x64"], ["win32", "arm64", "win-arm64"],
  ["darwin", "x64", "mac-x64"], ["darwin", "arm64", "mac-arm64"]]) {
  for (const channel of ["", "stable", "latest"]) {
    test(`platform channel, install and CLI agree: ${plat} ${channel || "default"}`, async (t) => {
      mockPlatform(t, platform, arch);
      process.env.CLOAKBROWSER_RELEASE_CHANNEL = channel;
      const version = plat.startsWith("linux-") ? "155.0.8059.39" : plat.startsWith("win-") ? "154.0.8037.97" :
        channel === "latest" ? "152.0.7977.75" : "151.0.7922.173";
      const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
      assert.equal(api.binaryInfo().version, version);
      assert.equal(api.binaryInfo().channel, channel || "stable");
      const chrome = await api.ensureBinary();
      assert.equal(chrome, binary.binaryPath(plat, join(cache, `v${version}`, plat)));
      assert.equal(api.binaryInfo().path, chrome);
      assert.equal(binary.hostFor(`v${version}`), host);
      delete process.env.CHROMIX_DOWNLOAD_HOST;
      assert.equal(binary.hostFor(`v${version}`), `https://github.com/xiaozhou26/Chromix/releases/download/v${version}`);
      const { execFileSync } = await import("node:child_process");
      const cli = new URL("../cli.js", import.meta.url).href;
      for (const command of ["info", "install"]) {
        const script = `Object.defineProperty(process, "platform", {value: ${JSON.stringify(platform)}});
          Object.defineProperty(process, "arch", {value: ${JSON.stringify(arch)}});
          process.argv = [process.execPath, "cli.js", ${JSON.stringify(command)}];
          await import(${JSON.stringify(cli)});`;
        const result = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
          encoding: "utf8", env: { ...process.env, CHROMIX_CACHE_DIR: cache },
        }).trim();
        if (command === "info") assert.deepEqual(JSON.parse(result), api.binaryInfo());
        else assert.equal(result, chrome);
      }
      assert.equal(urls.length, 2);
    });
  }

  for (const [channel, oldTag] of [["stable", "v151.0.7922.173"], ["latest", "v152.0.7977.75"]]) {
    test(`Platform channel promotion preserves old caches: ${plat} ${channel}`, async (t) => {
      mockPlatform(t, platform, arch);
      const oldRoot = join(cache, oldTag, plat), oldChrome = binary.binaryPath(plat, oldRoot);
      mkdirSync(dirname(oldChrome), { recursive: true });
      writeFileSync(oldChrome, "old browser");
      writeFileSync(join(oldRoot, binary.ASSETS[plat].launcher), "old launcher");
      const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
      const promoted = ["linux-x64", "linux-arm64", "win-x64", "win-arm64"].includes(plat);
      assert.equal(api.binaryInfo({ releaseChannel: channel }).installed, !promoted);
      const selectedTag = plat.startsWith("linux-") ? "v155.0.8059.39" : promoted ? "v154.0.8037.97" : oldTag;
      assert.equal(await api.ensureBinary({ releaseChannel: channel }), binary.binaryPath(plat, join(cache, selectedTag, plat)));
      assert.equal(urls.length, promoted ? 2 : 0);
      assert.equal(await api.ensureBinary({ browserVersion: oldTag }), oldChrome);
      assert.equal(readFileSync(oldChrome, "utf8"), "old browser");
      assert.equal(urls.length, promoted ? 2 : 0);
    });
  }

  for (const [version, expected] of [["151", "151.0.7922.173"], ["152", "152.0.7977.75"],
    ["151.0.7922.100", "151.0.7922.100"], ["v154.0.8037.97", "154.0.8037.97"],
    ["154.0.8037.97", "154.0.8037.97"]]) {
    test(`explicit versions remain independent of channel moves: ${plat} ${version}`, async (t) => {
      mockPlatform(t, platform, arch);
      const info = api.binaryInfo({ browserVersion: version });
      assert.equal(info.version, expected);
      if (version.includes(".")) assert.equal(info.channel, null);
      process.env.CLOAKBROWSER_VERSION = version;
      assert.deepEqual(api.binaryInfo(), info);
      const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
      const chrome = await api.ensureBinary({ browserVersion: version });
      assert.equal(chrome, binary.binaryPath(plat, join(cache, `v${expected}`, plat)));
      assert.equal(api.binaryInfo().path, chrome);
      assert.equal(urls.length, 2);
    });
  }

  test(`major, invalid selectors and precedence: ${plat}`, async (t) => {
    mockPlatform(t, platform, arch);
    if (["linux-x64", "linux-arm64", "win-x64", "win-arm64"].includes(plat)) assert.equal(api.binaryInfo({ browserVersion: "154" }).version, "154.0.8037.97");
    else assert.throws(() => api.binaryInfo({ browserVersion: "154" }), /Unsupported browser version/);
    for (const version of ["155", "v155"]) {
      if (plat.startsWith("linux-")) assert.equal(api.binaryInfo({ browserVersion: version }).version, "155.0.8059.39");
      else {
        assert.throws(() => api.binaryInfo({ browserVersion: version }), /Unsupported browser version/);
        await assert.rejects(api.ensureBinary({ browserVersion: version }), /Unsupported browser version/);
      }
    }
    for (const version of ["15", "999", "152.0", "../154.0.8037.97"]) {
      assert.throws(() => api.binaryInfo({ browserVersion: version }), /Unsupported browser version/);
      await assert.rejects(api.ensureBinary({ browserVersion: version }), /Unsupported browser version/);
    }
    assert.throws(() => api.binaryInfo({ releaseChannel: "unknown" }), /Unknown release channel/);
    assert.throws(() => api.binaryInfo({ releaseChannel: "toString" }), /Unknown release channel/);
    process.env.CLOAKBROWSER_RELEASE_CHANNEL = "latest";
    assert.equal(api.binaryInfo({ browserVersion: "151" }).channel, "latest");
    assert.equal(api.binaryInfo({ releaseChannel: "stable" }).channel, "stable");
    delete process.env.CLOAKBROWSER_RELEASE_CHANNEL;
    process.env.CLOAKBROWSER_VERSION = "151";
    assert.equal(api.binaryInfo({ browserVersion: "152" }).version, "152.0.7977.75");
  });

  test(`local override bypasses selection while info remains metadata: ${plat}`, async (t) => {
    mockPlatform(t, platform, arch);
    const local = join(cache, "local-chrome");
    writeFileSync(local, "local");
    process.env.CLOAKBROWSER_BINARY_PATH = local;
    assert.equal(await api.ensureBinary({ browserVersion: "invalid", releaseChannel: "invalid" }), local);
    const info = api.binaryInfo();
    assert.equal(info.version, plat.startsWith("linux-") ? "155.0.8059.39" : plat.startsWith("win-") ? "154.0.8037.97" : "151.0.7922.173");
    assert.equal(info.installed, false);
    assert.equal(info.path, null);
    rmSync(local);
    await assert.rejects(api.ensureBinary(), /does not exist/);
  });

  for (const scenario of ["linux-only", "matching", "older", "offline", "prerelease", "draft", "no-assets", "invalid"]) {
    test(`update requires newer platform asset: ${plat} ${scenario}`, async (t) => {
      mockPlatform(t, platform, arch);
      process.env.CLOAKBROWSER_RELEASE_CHANNEL = "latest";
      const current = plat.startsWith("linux-") ? "155.0.8059.39" : plat.startsWith("win-") ? "154.0.8037.97" : "152.0.7977.75";
      let candidate = scenario === "matching" ? "156.0.1.2" : "154.0.8037.97";
      if (scenario === "older") candidate = "151.0.7922.173";
      if (scenario === "invalid") candidate = "not-a-version";
      t.mock.method(globalThis, "fetch", async (url) => {
        assert.ok(url.endsWith("/releases/latest"));
        if (scenario === "offline") throw new Error("offline");
        return Response.json({ tag_name: `v${candidate}`, prerelease: scenario === "prerelease", draft: scenario === "draft",
          assets: scenario === "no-assets" ? [] : [{ name: binary.ASSETS[scenario === "linux-only" ? "linux-x64" : plat].asset }] });
      });
      assert.deepEqual(await api.checkForUpdate(), {
        currentVersion: current, latestVersion: scenario === "matching" ? candidate : current,
        updateAvailable: scenario === "matching",
      });
      assert.equal((await api.checkForUpdate({ browserVersion: "155.0.1.2", releaseChannel: "stable" })).currentVersion,
        plat.startsWith("linux-") ? "155.0.8059.39" : plat.startsWith("win-") ? "154.0.8037.97" : "151.0.7922.173");
    });
  }
}

for (const [arch, plat] of [["x64", "win-x64"], ["arm64", "win-arm64"]])
for (const [channel, oldTag] of [["stable", "v151.0.7922.173"], ["latest", "v152.0.7977.75"]]) {
  test(`Windows promotion failure never uses old channel cache: ${plat} ${channel}`, async (t) => {
    mockPlatform(t, "win32", arch);
    const oldRoot = join(cache, oldTag, plat), oldChrome = binary.binaryPath(plat, oldRoot);
    mkdirSync(dirname(oldChrome), { recursive: true });
    writeFileSync(oldChrome, "old browser");
    writeFileSync(join(oldRoot, "chromix", "chromix.cmd"), "old launcher");
    const urls = mockRelease(t, plat, Buffer.alloc(0), "http");
    await assert.rejects(api.ensureBinary({ releaseChannel: channel }), /download failed: 404/);
    const info = api.binaryInfo({ releaseChannel: channel });
    assert.equal(info.version, "154.0.8037.97");
    assert.equal(info.installed, false);
    assert.equal(info.path, null);
    assert.equal(await api.ensureBinary({ browserVersion: oldTag }), oldChrome);
    assert.equal(readFileSync(oldChrome, "utf8"), "old browser");
    assert.deepEqual(urls, [`${host}/chromix-${plat}.zip`]);
    assert.deepEqual(readdirSync(join(cache, "v154.0.8037.97")), []);
  });
}

test("explicit unavailable release fails without falling back", async (t) => {
  mockPlatform(t, "win32", "arm64");
  const urls = mockRelease(t, "win-arm64", Buffer.alloc(0), "http");
  await assert.rejects(api.ensureBinary({ browserVersion: "154.0.8037.97" }), /download failed: 404/);
  assert.deepEqual(urls, [`${host}/chromix-win-arm64.zip`]);
  assert.equal(api.binaryInfo({ browserVersion: "154.0.8037.97" }).version, "154.0.8037.97");
});

test("local override works even on unsupported platforms", async (t) => {
  mockPlatform(t, "freebsd", "x64");
  const local = join(cache, "local-chrome");
  writeFileSync(local, "local");
  process.env.CLOAKBROWSER_BINARY_PATH = local;
  assert.equal(await api.ensureBinary(), local);
  assert.equal(api.binaryInfo().platform, "unknown");
});

test("updates use exact environment and per-call versions", async (t) => {
  mockPlatform(t, "linux", "x64");
  process.env.CLOAKBROWSER_VERSION = "151.0.7922.100";
  t.mock.method(globalThis, "fetch", async () => Response.json({
    tag_name: "v154.0.8037.97", assets: [{ name: "chromix-linux-x64.zip" }],
  }));
  assert.deepEqual(await api.checkForUpdate(), {
    currentVersion: "151.0.7922.100", latestVersion: "154.0.8037.97", updateAvailable: true,
  });
  assert.deepEqual(await api.checkForUpdate({ browserVersion: "155.0.1.2" }), {
    currentVersion: "155.0.1.2", latestVersion: "155.0.1.2", updateAvailable: false,
  });
  Object.defineProperty(process, "platform", { value: "freebsd", configurable: true });
  assert.equal((await api.checkForUpdate()).updateAvailable, false);
});


for (const [arch, plat] of [["x64", "linux-x64"], ["arm64", "linux-arm64"]])
for (const version of ["155", "v155", "155.0.8059.39", "v155.0.8059.39", "154", "v154"]) {
  test(`Linux release selector installs exact tag: ${plat} ${version}`, async (t) => {
    mockPlatform(t, "linux", arch);
    const expected = version.replace(/^v/, "") === "154" ? "154.0.8037.97" : "155.0.8059.39";
    process.env.CLOAKBROWSER_VERSION = version;
    const info = api.binaryInfo();
    assert.equal(info.version, expected);
    assert.equal(info.channel, ["155", "v155"].includes(version) ? "stable" : null);
    const urls = mockRelease(t, plat, zipFixture(bundle(plat)));
    assert.equal(await api.ensureBinary(), binary.binaryPath(plat, join(cache, `v${expected}`, plat)));
    assert.equal(api.binaryInfo().installed, true);
    assert.deepEqual(urls, [`${host}/chromix-${plat}.zip`, `${host}/SHA256SUMS`]);
  });
}

for (const [platform, arch, plat] of [["linux", "x64", "linux-x64"], ["linux", "arm64", "linux-arm64"],
  ["win32", "x64", "win-x64"], ["win32", "arm64", "win-arm64"],
  ["darwin", "x64", "mac-x64"], ["darwin", "arm64", "mac-arm64"]]) {
  for (const version of ["155.0.8059.39", "v155.0.8059.39"]) {
    test(`missing exact 155 never uses available 154: ${plat} ${version}`, async (t) => {
      mockPlatform(t, platform, arch);
      const oldRoot = join(cache, "v154.0.8037.97", plat), oldChrome = binary.binaryPath(plat, oldRoot);
      mkdirSync(dirname(oldChrome), { recursive: true });
      writeFileSync(oldChrome, "old browser");
      writeFileSync(join(oldRoot, binary.ASSETS[plat].launcher), "old launcher");
      const urls = mockRelease(t, plat, Buffer.alloc(0), "http");
      await assert.rejects(api.ensureBinary({ browserVersion: version }), /download failed: 404/);
      const info = api.binaryInfo({ browserVersion: version });
      assert.equal(info.version, "155.0.8059.39");
      assert.equal(info.channel, null);
      assert.equal(info.installed, false);
      assert.equal(info.path, null);
      assert.equal(await api.ensureBinary({ browserVersion: "154.0.8037.97" }), oldChrome);
      assert.equal(readFileSync(oldChrome, "utf8"), "old browser");
      assert.deepEqual(urls, [`${host}/chromix-${plat}.zip`]);
    });
  }

  test(`Linux-only 155 release is not an update for other platforms: ${plat}`, async (t) => {
    mockPlatform(t, platform, arch);
    t.mock.method(globalThis, "fetch", async () => Response.json({
      tag_name: "v155.0.8059.39",
      assets: [{ name: "chromix-linux-x64.zip" }, { name: "chromix-linux-arm64.zip" }],
    }));
    assert.deepEqual(await api.checkForUpdate({ browserVersion: "154.0.8037.97" }), {
      currentVersion: "154.0.8037.97",
      latestVersion: plat.startsWith("linux-") ? "155.0.8059.39" : "154.0.8037.97",
      updateAvailable: plat.startsWith("linux-"),
    });
  });
}
