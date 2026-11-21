import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import zlib from "node:zlib";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { referenceAcceptanceProbe, validateReferenceAcceptance, validLayoutPolicyEvidence } from "../fingerprint_reference_acceptance.mjs";
import { audioAcceptanceProbe, validateAudioAcceptance } from "../fingerprint_audio_acceptance.mjs";
import { fontAcceptanceProbe, validateFontAcceptance } from "../fingerprint_font_acceptance.mjs";
import { summarizePositiveAcceptance, verifyResults, scenarios, BASIC_SCENARIOS, POSITIVE_GROUPS } from "../verify_fingerprint_modes_runtime.mjs";

const oracle = JSON.parse(fs.readFileSync(new URL("./fixtures/fingerprint-runtime-v1/positive-oracles.json", import.meta.url)));
const clone = value => JSON.parse(JSON.stringify(value));
test("native layout evidence rejects missing, changed, and unstable measurements", () => {
  const offset = [[120,80],[50,50]].map(([width,height]) => {
    const first = [width,height,width,height,width,height];
    return {width,height,first,repeat:[...first]};
  });
  const record = {all_native:{extra:{reference:{offset}}}};
  assert.ok(validateReferenceAcceptance(record).checks.every(check=>check.passed));
  offset[0].repeat[0]++;
  assert.equal(validateReferenceAcceptance(record).checks[0].passed,false);
  offset.pop();
  assert.equal(validateReferenceAcceptance(record).checks[1].passed,false);
});
test("multi-rectangle and quota observations reject changed bounds and ignored overrides",()=>{
  const first=[[[0,0,9,12],[0,12,10,12]],[[0,0,9,12],[0,12,10,12]]];
  const original={version:1,empty:[[0,0,0,0],[0,0,0,0]],first,repeat:structuredClone(first),quota:64*1024*1024};
  const check=s=>validLayoutPolicyEvidence({id:"memory_custom",extra:{slim_policy:s}});
  assert.ok(check(original));
  for(const change of [s=>s.empty[0][2]=0.5,s=>s.first[1].pop(),s=>s.repeat[0][0][2]++,s=>s.quota++]) {
    const modified=structuredClone(original);change(modified);assert.equal(check(modified),false);
  }
});
test("a partial report cannot become full by counting an unrelated positive", () => {
  const actual = [{ scenario: "audio_native_macos", name: "match_5000_native_reference_bytes", passed: true, acceptance_group: "native_audio" }];
  const missing = [{ scenario: "audio_native_macos", name: "match_1000_native_reference_bytes", reason: "Native PCM mismatch", acceptance_group: "native_audio" }];
  const result = summarizePositiveAcceptance(actual, missing);
  assert.equal(result.positive_acceptance.native_audio.status, "not_tested");
  assert.equal(result.full_strategy_acceptance, false);
  assert.equal(result.partial, true);
  actual[0].passed = false;
  assert.equal(summarizePositiveAcceptance(actual, missing).positive_acceptance.native_audio.status, "failed");
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
  assert.equal(matrix.length, 25);
  assert.equal(new Set(matrix.map(s => s.id)).size, matrix.length);
  assert.equal(BASIC_SCENARIOS.size, 15);
  for (const [id, seed] of [["fonts_default9918","9918"],["fonts_default14666","14666"],["fonts_native10026","10026"]]) {
    const flags = matrix.find(s=>s.id===id).flags;
    assert.ok(BASIC_SCENARIOS.has(id) && flags.includes("--fingerprint="+seed));
    assert.ok(!flags.some(arg=>/fonts-(?:mode-seed|allowlist|experimental-packages)/.test(arg)));
  }
  assert.equal(POSITIVE_GROUPS.length, 19);
  assert.ok(matrix.filter(s => BASIC_SCENARIOS.has(s.id)).every(s => s.flags.includes("--fingerprint-canvas-mode=native") || s.flags.includes("--fingerprint=off")));
  assert.equal(BASIC_SCENARIOS.has("canvas_shardx"), false);
  assert.equal(BASIC_SCENARIOS.has("webgl_shardx"), false);
});
test("repaired Shard WebGL requires positive sparse noise, stable coordinates and matching export", () => {
  const mask = oracle.shardWebGL.xor1_byte_offsets;
  assert.equal(mask.length, 52);
  assert.equal(new Set(mask).size, 52);
  assert.ok(mask.every(i => Number.isInteger(i) && i >= 0 && i < 16384 && i % 4 !== 3));
  assert.equal(oracle.audioReferencePairs.length, 3);
  assert.equal(oracle.cloakTextWindows.length, 11);
  const native = Buffer.alloc(16384);
  for (let p = 0; p < 4096; p++) native.set([(p*7)%180+20, (p*11)%180+20, (p*13)%180+20, 255], p * 4);
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
  const semantic = JSON.parse(execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-c",
    "import json; from tools.tests.pixel_semantics_fixtures import pixel_fixture; print(json.dumps({k:pixel_fixture(k) for k in ['all_native','webgl_shardx']}))"],
    {cwd:fileURLToPath(new URL("../..",import.meta.url)),encoding:"utf8",maxBuffer:4*1024*1024}));
  base.extra = {pixel_semantics:semantic.all_native}; target.extra = {pixel_semantics:semantic.webgl_shardx};
  const result = verifyResults([base, target], false, oracle);
  assert.equal(result.positive_acceptance.shard_webgl.status, "observed_pass");
  target.probe.webgl.webgl.crop = Buffer.alloc(crop.length).toString("base64");
  assert.equal(verifyResults([base, target], false, oracle).positive_acceptance.shard_webgl.status, "failed");
  target.probe.webgl.webgl.crop = crop.toString("base64");
  target.probe.webgl.webgl.full = native.toString("base64");
  assert.equal(verifyResults([base, target], false, oracle).positive_acceptance.shard_webgl.status, "failed");
});

test("native retirement replacements require real matching observations",async()=>{
  const {validateNativeSurfaceResults,retirementCases,scenarios}=await import('../verify_fingerprint_modes_runtime.mjs');
  const local={supported:true,entries:[{family:'Arial',loaded:true,status:'loaded'},{family:'DefinitelyAbsentFontXYZ',loaded:false,error:{name:'NetworkError'}}]};
  const fonts={version:1,windowLocal:structuredClone(local),workerLocal:structuredClone(local),iframeLocal:structuredClone(local),localFonts:{supported:true,records:[{family:'Arial',postscriptName:'ArialMT',fullName:'Arial',style:'Regular'}]},cssNodes:[{nodeId:'font1',family:'Arial',width:12,downloaded:false}],cssPlatformFonts:{font1:{fonts:[{familyName:'Arial',glyphCount:3}]}},ownedFont:{loaded:true,width:16,cssWidth:16}};
  const audio={version:1,referenceCases:[['match_5000',5000],['match_1000',1000],['match_44100',44100]].map(([id,length])=>({id,spec:{id,length},sampleRate:44100,numberOfChannels:1,length,byteLength:length*4,sha256:'a'.repeat(64),copySha256:'a'.repeat(64),state:'closed'}))};
  const fixture=()=>({all_native:{extra:{fonts:structuredClone(fonts),audio:structuredClone(audio)}},fonts_native10026:{extra:{fonts:structuredClone(fonts)}},audio_native_macos:{extra:{audio:structuredClone(audio)}}});
  const valid=validateNativeSurfaceResults(fixture());assert.equal(valid.not_tested.length,0);assert.ok(valid.checks.length>=10&&valid.checks.every(row=>row.passed));
  for(const mutate of [r=>delete r.fonts_native10026.extra.fonts,r=>r.fonts_native10026.extra.fonts.workerLocal.entries[0].loaded=false,r=>r.fonts_native10026.extra.fonts.localFonts.records.pop(),r=>r.audio_native_macos.extra.audio.referenceCases[0].sha256='b'.repeat(64),r=>r.audio_native_macos.extra.audio.referenceCases.pop()]){
    const records=fixture();mutate(records);assert.ok(validateNativeSurfaceResults(records).checks.some(row=>!row.passed));
  }
  const rejected=retirementCases();assert.equal(rejected.length,86);assert.equal(new Set(rejected.map(row=>row.id)).size,86);
  for(const surface of ["canvas","webgl","text","rects","sensors","offset","voices"])
    for(const prefix of ["fingerprint","uxr"])
      for(const mode of ["chromix","cloak"])
        assert.ok(rejected.some(row=>row.argument==="--"+prefix+"-"+surface+"-mode="+mode));
  assert.ok(rejected.some(row=>row.argument==='--uxr-canvas-bridge-unsafe=false'));
  assert.ok(scenarios().every(row=>!row.flags.some(flag=>/^--(?:fingerprint|uxr)-(?:fonts|audio|media)-mode=(?:chromix|cloak)$/.test(flag))));
});
