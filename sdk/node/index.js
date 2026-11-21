// chromix (Node) — drive the Chromix stealth Chromium engine with a
// CloakBrowser-compatible API.
//
// Mirrors the `cloakbrowser` JS wrapper (github.com/CloakHQ/CloakBrowser) so
// existing CloakBrowser scripts run on Chromix by changing only the import:
//
//   - import { launch } from "cloakbrowser";
//   + import { launch } from "@xiaoxiaofeihh/chromix";
//
// Same function names, same option names (camelCase, contextOptions/
// launchOptions nesting), same return types (Playwright Browser / BrowserContext
// via playwright-core — imported lazily so the SDK itself stays
// dependency-free). The --fingerprint-* flags emitted here are normalized to
// the engine's --uxr-* persona switches by patch 0036.
//
// Intentional differences: licenseKey is accepted and ignored (one open tier),
// geoip uses ip-api.com instead of a local GeoLite2 database.
// Puppeteer has a separate, native-driver entry point at ./puppeteer.
import { randomBytes } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";
import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { profileSeed } from "./_profile.js";
import {
  VERSION as BROWSER_VERSION, ASSETS, CACHE, hostFor, resolvePlatform, ensureNative,
  binaryPath, bundleComplete, resolveRelease, newerVersion,
} from "./_binary.js";
import { fontLaunchEnv } from "./_fonts.js";
import { ensurePersonaGeometry } from "./_persona.js";
import { normalizeFingerprintArgs } from "./_fingerprint.js";
import { extractProxyUrl, geoipHttp, networkArgs, splitProxy, lookupProxy as proxyForLookup,
  resolveWebrtcArgs } from "./_network.js";
import { launchMeasured, measuredOptions } from "./_device_pool.js";
import { browserProxy, hasRetiredProxyAuth } from "./_socks_auth.js";
export { exportCookies, importCookies, encryptCookies, decryptCookies } from "./cookies.js";

// One geometry pick per options object, shared by buildLaunchOptions and
// buildContextOptions (launchPersistentContext calls them separately).
const _personaGeoCache = new WeakMap();
function personaGeometryFor(options) {
  const rawArgs = options.launchOptions?.args ?? options.args ?? [];
  const args = normalizeFingerprintArgs(rawArgs);
  const key = JSON.stringify([args, options.stealthArgs]);
  let entry = _personaGeoCache.get(options);
  if (!entry || entry.key !== key) {
    const fingerprint = args.filter((a) => a.split("=", 1)[0] === "--fingerprint").at(-1);
    if (options.stealthArgs === false || fingerprint === "--fingerprint=off" ||
        !args.includes("--uxr-synthetic-device-tests=true")) {
      entry = { args, geometry: null };
    } else {
      entry = ensurePersonaGeometry(buildArgs({ extraArgs: args }));
    }
    entry.key = key;
    _personaGeoCache.set(options, entry);
  }
  return entry;
}

export const VERSION = "0.1.5";
export const CHROMIUM_VERSION = "152";
export const CHROMIUM_BUILD_VERSION = BROWSER_VERSION;
export const DEFAULT_VIEWPORT = { width: 1920, height: 947 };

// ---------------------------------------------------------------------------
// Binary management (CLOAKBROWSER_* env aliases)
// ---------------------------------------------------------------------------

function chromeBinaryPath(plat, tag) {
  return binaryPath(plat, join(CACHE, tag, plat));
}

export async function ensureBinary({ browserVersion, releaseChannel } = {}) {
  const explicit = process.env.CLOAKBROWSER_BINARY_PATH;
  if (explicit) {
    if (!existsSync(explicit)) throw new Error(`CLOAKBROWSER_BINARY_PATH does not exist: ${explicit}`);
    return explicit;
  }
  const plat = resolvePlatform();
  if (!plat) throw new Error("No native Chromix binary for this platform (Linux x64/arm64, Windows x64/arm64, macOS x64/arm64); or point CLOAKBROWSER_BINARY_PATH at a local build.");
  const { tag } = resolveRelease(plat, browserVersion, releaseChannel);
  const chrome = chromeBinaryPath(plat, tag);
  await ensureNative(plat, hostFor(tag), tag);
  if (!existsSync(chrome)) throw new Error(`bundle extracted but chrome binary missing: ${chrome}`);
  return chrome;
}

export function binaryInfo({ browserVersion, releaseChannel } = {}) {
  const plat = resolvePlatform() || "unknown";
  const { channel: ch, tag } = resolveRelease(plat, browserVersion, releaseChannel);
  const path = chromeBinaryPath(plat, tag);
  const installed = bundleComplete(plat, join(CACHE, tag, plat));
  return { tier: "open-source", version: tag.replace(/^v/, ""), channel: ch,
           platform: plat, path: installed ? path : null,
           installed, cacheDir: CACHE };
}

export function clearCache() { rmSync(CACHE, { recursive: true, force: true }); }

export async function checkForUpdate({ browserVersion, releaseChannel } = {}) {
  const plat = resolvePlatform() || "unknown";
  const { tag: current } = resolveRelease(plat, browserVersion, releaseChannel);
  let latest = current;
  try {
    const r = await fetch("https://api.github.com/repos/xiaozhou26/Chromix/releases/latest",
      { headers: { Accept: "application/vnd.github+json" } });
    if (r.ok) {
      const release = await r.json();
      const asset = ASSETS[plat]?.asset;
      if (asset && release.assets?.some((item) => item.name === asset) &&
          !release.draft && !release.prerelease && newerVersion(release.tag_name, current))
        latest = release.tag_name;
    }
  } catch { /* offline */ }
  return { currentVersion: current.replace(/^v/, ""), latestVersion: latest.replace(/^v/, ""),
           updateAvailable: latest !== current };
}

// ---------------------------------------------------------------------------
// Stealth args
// ---------------------------------------------------------------------------

export function getDefaultStealthArgs() {
  const seed = randomBytes(4).readUInt32LE(0) || 1;
  const base = [`--fingerprint=${seed}`];
  const platform = { linux: "linux", win32: "windows", darwin: "macos" }[process.platform];
  return platform ? [...base, `--fingerprint-platform=${platform}`] : base;
}

export function buildArgs({ stealthArgs = true, extraArgs = [], timezone, locale,
                            headless = true, extensionPaths, startMaximized = false } = {}) {
  extraArgs = networkArgs(extraArgs);
  const seen = new Map();
  const put = (arg) => seen.set(arg.split("=", 1)[0], arg);
  if (stealthArgs) for (const a of getDefaultStealthArgs()) put(a);
  for (const a of extraArgs || []) put(a);
  if (timezone) put(`--fingerprint-timezone=${timezone}`);
  if (locale) { put(`--lang=${locale}`); put(`--fingerprint-locale=${locale}`); }
  if (extensionPaths?.length) {
    const val = extensionPaths.map((p) => resolveAbs(p)).join(",");
    put(`--load-extension=${val}`);
    put(`--disable-extensions-except=${val}`);
  }
  const keys = [...seen.keys()];
  if (startMaximized && !["--start-maximized", "--window-size", "--window-position"].some((k) => keys.includes(k)))
    put("--start-maximized");
  return normalizeFingerprintArgs([...seen.values()], { final: true });
}

function resolveAbs(p) {
  try { return new URL(`file://${p}`).pathname; } catch { return p; }
}

// ---------------------------------------------------------------------------
// GeoIP metadata and WebRTC presentation-address resolution
// ---------------------------------------------------------------------------

// Explicit proxy always wins over environment/bypass settings; no proxy means direct.
// Lookup failures reject. The returned IP is metadata, not a routing guarantee.
export async function maybeResolveGeoip(geoip, proxy, timezone, locale, args) {
  if (!geoip) return { timezone, locale, exitIp: null };
  if (timezone == null && args) {
    const f = args.filter((a) => a.startsWith("--fingerprint-timezone=") || a.startsWith("--uxr-timezone=")).at(-1);
    if (f) timezone = f.split("=").slice(1).join("=");
  }
  if (locale == null && args) {
    const f = args.filter((a) => a.startsWith("--lang=") || a.startsWith("--fingerprint-locale=") || a.startsWith("--uxr-locale=")).at(-1);
    if (f) locale = f.split("=").slice(1).join("=");
  }
  const geo = await geoipHttp(extractProxyUrl(proxy));
  if (!geo) throw new Error("GeoIP lookup failed; no direct fallback");
  return { timezone: timezone ?? geo.timezone, locale: locale ?? geo.locale, exitIp: geo.exitIp };
}

// ---------------------------------------------------------------------------
// Launch options assembly
// ---------------------------------------------------------------------------

function launchProxy(options) {
  return Object.hasOwn(options.launchOptions || {}, "proxy") ? options.launchOptions.proxy : options.proxy;
}

function geoipProxy(options) {
  return Object.hasOwn(options.contextOptions || {}, "proxy") ? options.contextOptions.proxy : launchProxy(options);
}

function effectiveHeadless(options) {
  return options?.launchOptions?.headless ?? options?.headless ?? true;
}

export function buildContextOptions(options = {}) {
  if (Object.hasOwn(options, "devicePool"))
    throw new Error("devicePool requires launchContext or launchPersistentContext for runtime verification");
  const headless = effectiveHeadless(options);
  // Context-level locale/timezoneId would route through CDP emulation — strip
  // them and route through the binary flags instead (same policy as CloakBrowser).
  const { locale, timezoneId, ...ctx } = options.contextOptions || {};
  browserProxy(ctx.proxy);
  if (locale !== undefined || timezoneId !== undefined)
    console.warn("[chromix] contextOptions.locale/timezoneId ignored — use top-level locale/timezone (binary flag)");
  // Explicit synthetic viewport wins over the UI-strip template. Keep screen
  // and DPR together so Playwright does not replace screen with viewport size.
  const persona = personaGeometryFor(options).geometry;
  const personaViewport = persona
    ? { width: persona.viewportWidth, height: persona.viewportHeight } : null;
  const viewport = options.viewport !== undefined
    ? options.viewport
    : ctx.viewport !== undefined ? ctx.viewport : headless ? personaViewport : null;
  return {
    ...ctx,
    ...(ctx.proxy ? { proxy: splitProxy(ctx.proxy) } : {}),
    ...(options.userAgent ? { userAgent: options.userAgent } : {}),
    viewport,
    ...(viewport && persona ? {
      screen: ctx.screen ?? { width: persona.width, height: persona.height },
      deviceScaleFactor: ctx.deviceScaleFactor ?? persona.dpr,
    } : {}),
    ...(options.colorScheme ? { colorScheme: options.colorScheme } : {}),
  };
}

export async function buildLaunchOptions(options = {}) {
  if (Object.hasOwn(options, "devicePool"))
    throw new Error("devicePool requires launchContext or launchPersistentContext for runtime verification");
  const headless = effectiveHeadless(options);
  const proxy = browserProxy(launchProxy(options));
  const persona = personaGeometryFor(options);
  browserProxy(options.contextOptions?.proxy);
  if (hasRetiredProxyAuth(options.launchOptions?.env))
    throw new Error("The native SOCKS5 authentication environment option is retired");
  const lookupProxy = splitProxy(options.geoip
    ? proxyForLookup(persona.args, geoipProxy(options)) : geoipProxy(options));
  let args = networkArgs(persona.args, proxy || lookupProxy);
  const { timezone, locale, exitIp } = await maybeResolveGeoip(
    options.geoip, lookupProxy, options.timezone ?? options.timezoneId, options.locale, args);
  args = await resolveWebrtcArgs(args, lookupProxy, { exitIp, geoip: options.geoip, lookup: geoipHttp });
  const binary = options.launchOptions?.executablePath ?? await ensureBinary(options);
  if (typeof binary !== "string" || !existsSync(binary))
    throw new Error("executablePath must name an existing browser executable");
  // Headed windows use the same outer geometry as the persona.
  if (!headless && persona.geometry && !(args || []).some((a) => a.startsWith("--window-size"))) {
    args = [`--window-size=${persona.geometry.outerWidth},${persona.geometry.outerHeight}`, ...(args || [])];
  }
  let chromeArgs;
  if (options.stealthArgs === false && options.launchOptions?.args !== undefined) {
    const explicit = new Map(args.map(arg => [arg.split("=", 1)[0], arg]));
    if (timezone) explicit.set("--fingerprint-timezone", `--fingerprint-timezone=${timezone}`);
    if (locale) {
      explicit.set("--lang", `--lang=${locale}`);
      explicit.set("--fingerprint-locale", `--fingerprint-locale=${locale}`);
    }
    chromeArgs = [...explicit.values()];
  } else {
    chromeArgs = buildArgs({
      stealthArgs: options.stealthArgs ?? true,
      extraArgs: args,
      timezone, locale, headless,
      extensionPaths: options.extensionPaths,
      startMaximized: options.startMaximized ?? true,
    });
  }
  chromeArgs = normalizeFingerprintArgs(chromeArgs, { final: true });
  const env = fontLaunchEnv(binary, options.launchOptions?.env, options.fontsDir);
  return {
    executablePath: binary,
    headless,
    ignoreDefaultArgs: ["--enable-automation"],
    ...options.launchOptions,
    ...(proxy ? { proxy } : {}),
    args: chromeArgs,
    ...(env ? { env } : {}),
  };
}

// ---------------------------------------------------------------------------
// Humanize (behavioral layer)
// ---------------------------------------------------------------------------

const HUMAN_PRESETS = {
  default: { typingDelay: 70, typingSpread: 40, pauseChance: 0.1, wobble: 1.5,
             overshoot: 0.35, minSteps: 8, stepsDivisor: 12, aimDelay: 80,
             hold: 100, mistype: 0.02, scrollPause: 300 },
  careful: { typingDelay: 130, typingSpread: 60, pauseChance: 0.15, wobble: 1.0,
             overshoot: 0.15, minSteps: 10, stepsDivisor: 8, aimDelay: 200,
             hold: 180, mistype: 0.04, scrollPause: 500 },
};

function mergeHumanConfig(base, overrides) {
  if (overrides === undefined) return { ...base };
  if (!overrides || typeof overrides !== "object" || Array.isArray(overrides))
    throw new TypeError("humanConfig must be an object");
  const result = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    if (!Object.hasOwn(HUMAN_PRESETS.default, key) && key !== "seed")
      throw new TypeError(`Unsupported humanConfig option: ${key}`);
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 ||
        (["pauseChance", "overshoot", "mistype"].includes(key) && value > 1) ||
        (key === "stepsDivisor" && value === 0) ||
        (key === "minSteps" && (!Number.isInteger(value) || value < 1)) ||
        (key === "seed" && (!Number.isInteger(value) || value > 0xffffffff)))
      throw new TypeError(`Invalid humanConfig.${key}`);
    result[key] = value;
  }
  return result;
}

export function resolveHumanConfig(preset = "default", humanConfig) {
  if (!Object.hasOwn(HUMAN_PRESETS, preset))
    throw new TypeError(`Unsupported humanPreset: ${preset}`);
  return mergeHumanConfig(HUMAN_PRESETS[preset], humanConfig);
}

const humanPages = new WeakSet();
const humanContexts = new WeakSet();
const humanBrowsers = new WeakSet();
const nativeHumanCall = new AsyncLocalStorage();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ease = (t) => t * t * (3 - 2 * t);

function bezierPath(p0, p3, steps, rand) {
  const pts = [];
  const dx = p3.x - p0.x, dy = p3.y - p0.y;
  const dist = Math.hypot(dx, dy) || 1;
  const nx = -dy / dist, ny = dx / dist;
  const bow = (0.15 + rand() * 0.35) * dist * (rand() < 0.5 ? -1 : 1);
  const c1 = { x: p0.x + dx * 0.3 + nx * bow * 0.5, y: p0.y + dy * 0.3 + ny * bow * 0.5 };
  const c2 = { x: p0.x + dx * 0.7 + nx * bow * 0.4, y: p0.y + dy * 0.7 + ny * bow * 0.4 };
  for (let i = 1; i <= steps; i++) {
    const t = ease(i / steps), u = 1 - t;
    pts.push({
      x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p3.x,
      y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p3.y,
    });
  }
  return pts;
}

export function humanizePage(page, config = resolveHumanConfig(),
    { driver = typeof page.browserContext === "function" && typeof page.context !== "function"
      ? "puppeteer" : "playwright" } = {}) {
  if (humanPages.has(page)) return page;
  const base = mergeHumanConfig(HUMAN_PRESETS.default, config);
  const random = mulberry32(base.seed ?? Math.floor(Math.random() * 2 ** 31));
  const state = { x: 0, y: 0 };
  const mouse = page.mouse, keyboard = page.keyboard;
  const raw = {};
  for (const name of ["move", "click", "dblclick", "wheel"])
    if (typeof mouse[name] === "function") raw[name] = mouse[name].bind(mouse);
  raw.type = keyboard.type.bind(keyboard);
  raw.press = keyboard.press.bind(keyboard);
  const native = (fn) => nativeHumanCall.run(page, fn);
  const callConfig = (opts = {}) => {
    const { humanConfig, ...options } = opts;
    const cfg = mergeHumanConfig(base, humanConfig);
    const rand = humanConfig?.seed === undefined ? random : mulberry32(cfg.seed);
    return { cfg, rand, options };
  };
  const timedOut = () => {
    const error = new Error("Humanized action timeout exceeded");
    error.name = "TimeoutError";
    throw error;
  };
  const checkDeadline = (deadline) => {
    if (performance.now() >= deadline) timedOut();
  };
  const wait = async (ms, deadline = Infinity) => {
    checkDeadline(deadline);
    const remaining = deadline - performance.now();
    await sleep(Math.ceil(Math.max(0, Math.min(ms, remaining))));
    if (ms >= remaining) timedOut();
    checkDeadline(deadline);
  };

  async function humanMove(x, y, cfg, rand, deadline = Infinity) {
    checkDeadline(deadline);
    const dist = Math.hypot(x - state.x, y - state.y);
    if (dist < 1) return;
    const steps = Math.max(cfg.minSteps, Math.floor(dist / cfg.stepsDivisor));
    const duration = 80 + Math.min(620, dist / 2.5) + rand() * 60;
    const pts = bezierPath({ x: state.x, y: state.y }, { x, y }, steps, rand);
    const t0 = performance.now();
    for (let i = 0; i < pts.length; i++) {
      checkDeadline(deadline);
      const w = rand() * cfg.wobble;
      const px = pts[i].x + (rand() * 2 - 1) * w;
      const py = pts[i].y + (rand() * 2 - 1) * w;
      await native(() => raw.move(px, py));
      state.x = px; state.y = py;
      const remaining = t0 + duration * Math.pow((i + 1) / pts.length, 1.15) - performance.now();
      if (remaining > 0) await wait(remaining, deadline);
    }
    if (dist > 60 && rand() < cfg.overshoot) {
      await native(() => raw.move(x + 2 + rand() * 4, y + 2 + rand() * 4));
      await wait(20 + rand() * 30, deadline);
    }
    checkDeadline(deadline);
    await native(() => raw.move(x, y));
    state.x = x; state.y = y;
  }

  mouse.move = async (x, y, opts = {}) => {
    if (nativeHumanCall.getStore() === page) return raw.move(x, y, opts);
    const { cfg, rand, options } = callConfig(opts);
    if (Object.keys(options).length) {
      const result = await native(() => raw.move(x, y, options));
      state.x = x; state.y = y;
      return result;
    }
    await humanMove(x, y, cfg, rand);
  };
  for (const name of ["click", "dblclick"]) {
    const click = raw[name] ?? (name === "dblclick" && raw.click
      ? (x, y, opts) => raw.click(x, y, { ...opts, ...(driver === "puppeteer" ? { count: 2 } : { clickCount: 2 }) }) : null);
    if (!click) continue;
    mouse[name] = async (x, y, opts = {}) => {
      if (nativeHumanCall.getStore() === page) return click(x, y, opts);
      const { cfg, rand, options } = callConfig(opts);
      if (Object.keys(options).some(key => key !== "button")) {
        const result = await native(() => click(x, y, options));
        state.x = x; state.y = y;
        return result;
      }
      await humanMove(x, y, cfg, rand);
      await sleep(cfg.aimDelay * (0.5 + rand()));
      return native(() => click(x, y, { ...options, delay: cfg.hold * (0.5 + rand()) }));
    };
  }
  async function humanWheel(dx, dy, opts) {
    const { cfg, rand, options } = callConfig(opts);
    if (Object.keys(options).length)
      throw new TypeError("Unsupported humanized wheel options");
    const wheel = (x, y) => native(() => driver === "puppeteer"
      ? raw.wheel({ deltaX: x, deltaY: y }) : raw.wheel(x, y));
    if (dy) {
      const steps = Math.max(4, Math.floor(Math.abs(dy) / 120));
      let done = 0;
      for (let i = 1; i <= steps; i++) {
        const target = i === steps ? dy : Math.trunc(dy * ease(i / steps));
        if (target - done) { await wheel(0, target - done); done = target; }
        await sleep(rand() < 0.06 ? cfg.scrollPause * (0.6 + rand()) : 20 + rand() * 40);
      }
    }
    if (dx) await wheel(dx, 0);
  }
  mouse.wheel = driver === "puppeteer"
    ? (opts = {}) => {
      if (nativeHumanCall.getStore() === page) return raw.wheel(opts);
      const { deltaX = 0, deltaY = 0, ...rest } = opts;
      return humanWheel(deltaX, deltaY, rest);
    }
    : (dx, dy, opts = {}) => nativeHumanCall.getStore() === page
      ? raw.wheel(dx, dy) : humanWheel(dx, dy, opts);
  keyboard.type = async (text, opts = {}) => {
    if (nativeHumanCall.getStore() === page) return raw.type(text, opts);
    const { cfg, rand, options } = callConfig(opts);
    if (Object.keys(options).length) return native(() => raw.type(text, options));
    for (const ch of String(text)) {
      if (/[a-z]/i.test(ch) && rand() < cfg.mistype) {
        await native(() => raw.type("qwertyuiopasdfghjklzxcvbnm"[Math.floor(rand() * 26)], { delay: 0 }));
        await sleep(150 + rand() * 250);
        await native(() => raw.press("Backspace"));
        await sleep(80 + rand() * 120);
      }
      await native(() => raw.type(ch, { delay: 0 }));
      let d = cfg.typingDelay + (rand() * 2 - 1) * cfg.typingSpread;
      if (rand() < cfg.pauseChance) d += 400 + rand() * 600;
      await sleep(Math.max(d, 0));
    }
  };
  keyboard.press = async (key, opts = {}) => {
    if (nativeHumanCall.getStore() === page) return raw.press(key, opts);
    const { cfg, rand, options } = callConfig(opts);
    if (!Object.keys(options).length) await sleep(cfg.aimDelay * (0.5 + rand()));
    return native(() => raw.press(key, options));
  };

  // Native trial and final dispatch retain Playwright's hit testing and retry semantics.
  if (driver === "playwright") for (const name of ["click", "dblclick"]) {
    if (typeof page[name] !== "function") continue;
    const action = page[name].bind(page);
    page[name] = async (selector, opts = {}) => {
      const { cfg, rand, options } = callConfig(opts);
      if (Object.keys(options).some(key => !["timeout", "strict"].includes(key)))
        return native(() => action(selector, options));
      if (options.timeout !== undefined && (typeof options.timeout !== "number" ||
          !Number.isFinite(options.timeout) || options.timeout < 0))
        throw new TypeError("timeout must be a nonnegative finite number");
      const deadline = options.timeout ? performance.now() + options.timeout : Infinity;
      const remainingOptions = () => {
        checkDeadline(deadline);
        return { ...options, ...(Number.isFinite(deadline)
          ? { timeout: Math.max(1, deadline - performance.now()) } : {}) };
      };
      await native(() => action(selector, { ...remainingOptions(), trial: true }));
      const handle = await page.$(selector, { strict: options.strict });
      try {
        const box = await handle?.boundingBox();
        if (box) await humanMove(box.x + box.width / 2, box.y + box.height / 2, cfg, rand, deadline);
      } finally { await handle?.dispose(); }
      await wait(cfg.aimDelay * (0.5 + rand()), deadline);
      const delay = cfg.hold * (0.5 + rand());
      if (delay >= deadline - performance.now()) {
        await wait(deadline - performance.now(), deadline);
        checkDeadline(deadline);
      }
      return native(() => action(selector, { ...remainingOptions(), delay }));
    };
  }
  humanPages.add(page);
  return page;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

async function humanizeContext(context, cfg, driver = "playwright") {
  if (humanContexts.has(context)) return context;
  const prepare = page => humanizePage(page, cfg, { driver });
  for (const page of await context.pages()) prepare(page);
  const newPage = context.newPage.bind(context);
  context.newPage = async (...args) => prepare(await newPage(...args));
  if (driver === "playwright") context.on?.("page", prepare);
  humanContexts.add(context);
  return context;
}

export async function humanizeBrowser(browser, config = resolveHumanConfig()) {
  if (humanBrowsers.has(browser)) return browser;
  const cfg = mergeHumanConfig(HUMAN_PRESETS.default, config);
  const driver = typeof browser.newContext === "function" ? "playwright" : "puppeteer";
  const contexts = driver === "playwright" ? browser.contexts() : browser.browserContexts();
  for (const context of contexts) await humanizeContext(context, cfg, driver);
  const newPage = browser.newPage.bind(browser);
  browser.newPage = async (...args) => humanizePage(await newPage(...args), cfg, { driver });
  const method = driver === "playwright" ? "newContext" : "createBrowserContext";
  const newContext = browser[method].bind(browser);
  browser[method] = async (...args) => humanizeContext(await newContext(...args), cfg, driver);
  humanBrowsers.add(browser);
  return browser;
}

// ---------------------------------------------------------------------------
// Launch family
// ---------------------------------------------------------------------------

async function loadChromium() {
  for (const mod of ["playwright-core", "playwright"]) {
    try { return (await import(mod)).chromium; } catch { /* try next */ }
  }
  throw new Error("playwright-core or playwright must be installed: npm i playwright-core");
}

export async function launch(options = {}) {
  if (options.humanize) resolveHumanConfig(options.humanPreset, options.humanConfig);
  options = { ...options };
  const chromium = await loadChromium();
  const launchOpts = await buildLaunchOptions(options);
  const browser = await chromium.launch(launchOpts);
  for (const method of ["newPage", "newContext"]) {
    if (!browser[method]) continue;
    const original = browser[method].bind(browser);
    browser[method] = (contextOptions = {}) => {
      const defaults = buildContextOptions(options);
      if (contextOptions.viewport === null) {
        delete defaults.deviceScaleFactor;
        delete defaults.screen;
      }
      return original({ ...defaults, ...contextOptions });
    };
  }
  if (options.humanize)
    await humanizeBrowser(browser, resolveHumanConfig(options.humanPreset, options.humanConfig));
  return browser;
}

export async function launchContext(options = {}) {
  if (options.humanize) resolveHumanConfig(options.humanPreset, options.humanConfig);
  if (Object.hasOwn(options, "devicePool")) {
    measuredOptions(options);
    return launchMeasured(await loadChromium(), await ensureBinary(options), options);
  }
  options = { ...options };
  const chromium = await loadChromium();
  const browser = await chromium.launch(await buildLaunchOptions(options));
  let ctx;
  try { ctx = await browser.newContext(buildContextOptions(options)); }
  catch (error) { await browser.close(); throw error; }
  if (options.humanize) {
    await humanizeContext(ctx, resolveHumanConfig(options.humanPreset, options.humanConfig));
  }
  const origClose = ctx.close.bind(ctx);
  ctx.close = async (...a) => { try { await origClose(...a); } finally { await browser.close(); } };
  return ctx;
}

export async function launchPersistentContext(options = {}) {
  if (options.humanize) resolveHumanConfig(options.humanPreset, options.humanConfig);
  if (Object.hasOwn(options, "devicePool")) {
    if (!options.userDataDir) throw new Error("launchPersistentContext requires options.userDataDir");
    measuredOptions(options);
    return launchMeasured(await loadChromium(), await ensureBinary(options), options);
  }
  const chromium = await loadChromium();
  if (!options.userDataDir) throw new Error("launchPersistentContext requires options.userDataDir");
  let args = options.contextOptions?.args ?? options.launchOptions?.args ?? options.args ?? [];
  if ((options.stealthArgs ?? true) && !args.some((a) => a.split("=", 1)[0] === "--fingerprint")) {
    args = [...args, `--fingerprint=${await profileSeed(options.userDataDir)}`];
  }
  const launchOptions = { ...options.launchOptions };
  if (options.contextOptions?.args !== undefined || options.launchOptions?.args !== undefined)
    launchOptions.args = args;
  const { args: contextArgs, ...contextOptions } = options.contextOptions || {};
  const prepared = { ...options, args, launchOptions, contextOptions };
  const launchOpts = { ...(await buildLaunchOptions(prepared)), ...buildContextOptions(prepared) };
  const ctx = await chromium.launchPersistentContext(options.userDataDir, launchOpts);
  if (options.humanize) {
    await humanizeContext(ctx, resolveHumanConfig(options.humanPreset, options.humanConfig));
  }
  return ctx;
}
