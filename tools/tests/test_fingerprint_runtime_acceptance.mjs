import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import zlib from "node:zlib";
import { referenceAcceptanceProbe, validateReferenceAcceptance } from "../fingerprint_reference_acceptance.mjs";
import { audioAcceptanceProbe, validateAudioAcceptance } from "../fingerprint_audio_acceptance.mjs";
import { fontAcceptanceProbe, validateFontAcceptance } from "../fingerprint_font_acceptance.mjs";
import { summarizePositiveAcceptance, verifyResults, scenarios, BASIC_SCENARIOS, POSITIVE_GROUPS } from "../verify_fingerprint_modes_runtime.mjs";

const oracle = JSON.parse(fs.readFileSync(new URL("./fixtures/fingerprint-runtime-v1/positive-oracles.json", import.meta.url)));
const clone = value => JSON.parse(JSON.stringify(value));
function referenceFixture() {
  const original = { version: 1, text: [], offset: [], cssPlatformFonts: {} };
  const changed = { version: 1, text: [], cssPlatformFonts: {} };
  for (const row of oracle.cloakTextWindows) {
    const nodeId = `reference-text-${row.index}`;
    const metrics = { ...row.expected };
    delete metrics.emHeightAscent; delete metrics.emHeightDescent;
    original.text.push({ index: row.index, nodeId, font: row.font,
      metrics: { ...metrics, fontBoundingBoxAscent: metrics.fontBoundingBoxAscent + 1 } });
    changed.text.push({ index: row.index, nodeId, font: row.font, metrics, repeat: { ...metrics } });
    original.cssPlatformFonts[nodeId] = { fonts: [{ familyName: row.family, glyphCount: row.text.length, isCustomFont: false }] };
    changed.cssPlatformFonts[nodeId] = clone(original.cssPlatformFonts[nodeId]);
  }
  for (const [width, height] of [[120, 80], [50, 50]]) {
    const first = { offsetWidth: width, offsetHeight: height, clientWidth: width, clientHeight: height,
      rectWidth: width, rectHeight: height };
    original.offset.push({ width, height, first, repeat: { ...first } });
  }
  const offset = clone(original.offset);
  offset[0].first.offsetWidth += 40; offset[0].first.offsetHeight -= 28;
  offset[0].repeat = { ...offset[0].first };
  const local = oracle.cloakVoicesWindowsByLocale["zh-CN"].filter(v => v.localService).map((v, index) => ({
    ...v, voiceURI: v.name, default: index === 0,
  }));
  original.voices = { supported: true, settled: true, values: local };
  const voice = values => ({ supported: true, settled: true, values, events: 1, emptyWaitCompleted: values.length === 0 });
  return {
    all_native: { extra: { reference: original } },
    cloak_text_positive: { extra: { reference: changed } },
    cloak_offset: { extra: { reference: { offset } } },
    cloak_voices: { flags: ["--fingerprint-locale=zh-CN"], voice_oracle_locale: "zh-CN",
      extra: { reference: { voices: voice(clone(local)) } } },
    cloak_voices_no_local: { flags: ["--fingerprint-locale=zh-CN"], voice_oracle_locale: "zh-CN",
      extra: { reference: { voices: voice([]) } } },
  };
}
test("captured reference positives require observable output, real glyph family, exact locale and local filtering", () => {
  const value = validateReferenceAcceptance(referenceFixture(), oracle);
  assert.ok(value.checks.length > 10);
  assert.ok(value.checks.every(c => c.passed));
  assert.deepEqual(Object.values(value.positive_acceptance).map(v => v.status), ["observed_pass", "observed_pass", "observed_pass"]);
});
test("a stable no-op cannot pass an eligible text reference", () => {
  const record = referenceFixture();
  const row = record.cloak_text_positive.extra.reference.text[0];
  row.metrics.fontBoundingBoxAscent += 1; row.repeat = { ...row.metrics };
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_text.status, "failed");
});
test("unexposed internal text fields do not fabricate an observable replacement", () => {
  const record = referenceFixture();
  record.all_native.extra.reference.text = clone(record.cloak_text_positive.extra.reference.text);
  const result = validateReferenceAcceptance(record, oracle);
  assert.equal(result.positive_acceptance.cloak_text.status, "not_tested");
  assert.equal(result.positive_acceptance.cloak_text.eligible_count, 0);
});
test("different native glyph families cannot make a strict text row eligible", () => {
  const record = referenceFixture();
  for (const value of Object.values(record.all_native.extra.reference.cssPlatformFonts)) value.fonts[0].familyName = "Native fallback";
  const result = validateReferenceAcceptance(record, oracle);
  assert.equal(result.positive_acceptance.cloak_text.status, "not_tested");
});
test("empty voice snapshots, mismatched capture locale and missing targets cannot pass", () => {
  let record = referenceFixture();
  record.all_native.extra.reference.voices.values = [];
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_voices.status, "not_tested");
  record = referenceFixture(); record.cloak_voices.voice_oracle_locale = "unsupported";
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_voices.status, "failed");
  record = referenceFixture(); record.cloak_voices_no_local.extra.reference.voices.values =
    clone(record.all_native.extra.reference.voices.values);
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_voices.status, "failed");
  record = referenceFixture(); record.cloak_voices_no_local.extra.reference.voices.events = 0;
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_voices.status, "failed");
});
test("offset bounds and actual unchanged layout are required", () => {
  const record = referenceFixture();
  record.cloak_offset.extra.reference.offset[1].first.offsetWidth++;
  assert.equal(validateReferenceAcceptance(record, oracle).positive_acceptance.cloak_offset.status, "failed");
});
test("a partial report cannot become full by counting an unrelated positive", () => {
  const actual = [{ scenario: "cloak_audio_positive", name: "reference_5000_full_pcm", passed: true, acceptance_group: "cloak_audio" }];
  const missing = [{ scenario: "cloak_audio_positive", name: "reference_1000_full_pcm", reason: "Native PCM mismatch", acceptance_group: "cloak_audio" }];
  const result = summarizePositiveAcceptance(actual, missing);
  assert.equal(result.positive_acceptance.cloak_audio.status, "not_tested");
  assert.equal(result.full_strategy_acceptance, false);
  assert.equal(result.partial, true);
  actual[0].passed = false;
  assert.equal(summarizePositiveAcceptance(actual, missing).positive_acceptance.cloak_audio.status, "failed");
});
test("optional text candidates do not erase a genuine distinguishable hit", () => {
  const actual = [{ scenario: "cloak_text_positive", name: "text_reference_100_eligible_replacement", passed: true, acceptance_group: "cloak_text" }];
  const missing = [{ scenario: "cloak_text_positive", name: "text_reference_233", reason: "Different font", acceptance_group: "cloak_text", optional_candidate: true }];
  assert.equal(summarizePositiveAcceptance(actual, missing).positive_acceptance.cloak_text.status, "observed_pass");
});
test("empty font/audio evidence is explicitly untested, never a positive", () => {
  for (const value of [validateAudioAcceptance({}, oracle), validateFontAcceptance({})]) {
    assert.ok(Object.values(value.positive_acceptance).every(v => v.status === "not_tested"));
    assert.ok(value.not_tested.length > 0);
    assert.equal(value.checks.length, 0);
  }
});
test("browser entrypoints serialize without importing Node helpers", () => {
  for (const fn of [referenceAcceptanceProbe, audioAcceptanceProbe, fontAcceptanceProbe])
    assert.equal(typeof new vm.Script(`(${fn.toString()})`).runInNewContext({}), "function");
});
test("the bounded matrix has distinct scenes and uses basic probes for independent additions", () => {
  const matrix = scenarios();
  assert.equal(matrix.length, 26);
  assert.equal(new Set(matrix.map(s => s.id)).size, matrix.length);
  assert.equal(BASIC_SCENARIOS.size, 15);
  assert.equal(POSITIVE_GROUPS.length, 25);
  assert.ok(matrix.filter(s => BASIC_SCENARIOS.has(s.id)).every(s => s.flags.includes("--fingerprint-canvas-mode=native")));
});
test("repaired Shard WebGL requires positive sparse noise, stable coordinates and matching export", () => {
  const mask = oracle.shardWebGL.xor1_byte_offsets;
  assert.equal(mask.length, 52);
  assert.equal(new Set(mask).size, 52);
  assert.ok(mask.every(i => Number.isInteger(i) && i >= 0 && i < 16384 && i % 4 !== 3));
  assert.equal(oracle.audioReferencePairs.length, 3);
  assert.equal(oracle.cloakTextWindows.length, 11);
  const native = Buffer.alloc(16384);
  for (let p = 0; p < 4096; p++) native.set([51, 102, 153, 255], p * 4);
  const noised = Buffer.from(native);
  // Independent invariant fixture, deliberately not a copy of the new PRNG.
  noised[0] ^= 1;
  noised[(13 * 64 + 11) * 4 + 1] ^= 1;
  noised[(63 * 64 + 63) * 4 + 2] ^= 1;
  const crop = Buffer.alloc(18 * 16 * 4);
  for (let y = 0; y < 16; y++) noised.copy(crop, y * 18 * 4, ((13+y)*64+11)*4, ((13+y)*64+29)*4);
  const header = Buffer.alloc(13); header.writeUInt32BE(64); header.writeUInt32BE(64, 4); header[8] = 8; header[9] = 6;
  const chunk = (name, data) => { const b = Buffer.alloc(data.length + 12); b.writeUInt32BE(data.length); b.write(name, 4); data.copy(b, 8); return b; };
  const scanlines = Buffer.alloc(64 * 257);
  for (let y = 0; y < 64; y++) noised.copy(scanlines, y * 257 + 1, (63-y) * 256, (64-y) * 256);
  const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(scanlines)), chunk("IEND", Buffer.alloc(0))]).toString("base64");
  const gl = data => ({ supported: true, full: data.toString("base64"), repeat: data.toString("base64"),
    crop: crop.toString("base64"), png, readError: 0, negative: { webgl2Enum: 1280 } });
  const base = { id: "all_native", probe: { webgl: { webgl: gl(native), webgl2: gl(native) } } };
  const target = { id: "webgl_shardx", probe: { webgl: { webgl: gl(noised), webgl2: gl(noised) } } };
  const result = verifyResults([base, target], false, oracle);
  assert.equal(result.positive_acceptance.shard_webgl.status, "observed_pass");
  target.probe.webgl.webgl.crop = Buffer.alloc(crop.length).toString("base64");
  assert.equal(verifyResults([base, target], false, oracle).positive_acceptance.shard_webgl.status, "failed");
  target.probe.webgl.webgl.crop = crop.toString("base64");
  target.probe.webgl.webgl.full = native.toString("base64");
  assert.equal(verifyResults([base, target], false, oracle).positive_acceptance.shard_webgl.status, "failed");
});
