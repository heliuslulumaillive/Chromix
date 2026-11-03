import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {validatePixelSemantics,validateHeadlessScreens,HEADLESS_SCENARIOS,PIXEL_SCENARIOS,pngDimensions} from '../pixel_semantic_acceptance.mjs';
import {decodePNG,scenarios,BASIC_SCENARIOS,POSITIVE_GROUPS} from '../verify_fingerprint_modes_runtime.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const code="import json; from tools.tests.pixel_semantics_fixtures import pixel_fixture, headless_fixture; a=['all_native','canvas_shardx','webgl_shardx']; b=['headless_custom_dpr2','headless_custom_fractional','headless_explicit_info','headless_force_scale','headless_native_ignored','headless_off_ignored']; print(json.dumps({**{k:{'extra':{'pixel_semantics':pixel_fixture(k)}} for k in a},**{k:{'extra':{'headless_screen':headless_fixture(k)}} for k in b}}))";
const fixtures=JSON.parse(execFileSync(process.env.PYTHON||'python',['-B','-X','utf8','-c',code],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024}));
const fresh=()=>structuredClone(fixtures);
const failures=r=>r.checks.filter(c=>!c.passed);

test('new checks retain full Canvas/WebGL scenarios and all former positive groups',()=>{
 for(const id of PIXEL_SCENARIOS)assert.equal(BASIC_SCENARIOS.has(id),false,id);
 for(const id of HEADLESS_SCENARIOS)assert.equal(BASIC_SCENARIOS.has(id),true,id);
 assert.equal(scenarios().length,32);assert.equal(POSITIVE_GROUPS.length,26);
 assert.ok(POSITIVE_GROUPS.includes('shard_audio'));assert.ok(POSITIVE_GROUPS.includes('cloak_voices'));
});
test('nonempty invented receipt exercises every pixel and headless obligation',()=>{
 const a=validatePixelSemantics(fresh(),decodePNG),b=validateHeadlessScreens(fresh());
 assert.equal(a.checks.length,104);assert.deepEqual(failures(a),[]);
 assert.equal(b.checks.length,32);assert.deepEqual(failures(b),[]);
});
test('missing observations and no-op protection do not become positive acceptance',()=>{
 const mutations=[p=>delete p.canvas_shardx.extra.pixel_semantics,p=>p.canvas_shardx.extra.pixel_semantics.canvas={},
  p=>p.canvas_shardx.extra.pixel_semantics.canvas.flat.full='',p=>p.canvas_shardx.extra.pixel_semantics.canvas.text.crops=[],
  p=>p.canvas_shardx.extra.pixel_semantics.canvas.gradient.crops[0]=null,
  p=>p.canvas_shardx.extra.pixel_semantics.canvas.text=structuredClone(p.all_native.extra.pixel_semantics.canvas.text),
  p=>p.webgl_shardx.extra.pixel_semantics.webgl.webgl.clear.full='',
  p=>p.webgl_shardx.extra.pixel_semantics.webgl.webgl2.pbo.state.rowLength=0,
  p=>{const pbo=p.webgl_shardx.extra.pixel_semantics.webgl.webgl2.pbo;const b=Buffer.from(pbo.raw,'base64');b[0]=1;pbo.raw=b.toString('base64');},
  p=>p.webgl_shardx.extra.pixel_semantics.webgl.webgl=structuredClone(p.all_native.extra.pixel_semantics.webgl.webgl)];
 for(const mutate of mutations){const f=fresh();mutate(f);assert.ok(failures(validatePixelSemantics(f,decodePNG)).length);}
});
test('unavailable GL is explicitly not tested, never invented as positive pixels',()=>{
 const f=fresh();for(const id of PIXEL_SCENARIOS)for(const kind of ['webgl','webgl2'])f[id].extra.pixel_semantics.webgl[kind]={supported:false,reason:'Native WebGL context unavailable'};
 const result=validatePixelSemantics(f,decodePNG);assert.equal(result.not_tested.length,2);assert.equal(failures(result).length,0);
 assert.ok(!result.checks.some(c=>c.name.startsWith('semantic_webgl')&&c.name.endsWith('rendered_positive')));
});
test('headless raster, PNG identity, caller precedence and Native/off are all required',()=>{
 const mutations=[p=>delete p.headless_custom_dpr2.extra.headless_screen,
  p=>p.headless_custom_dpr2.extra.headless_screen.screenshot.sha256='0'.repeat(64),
  p=>p.headless_custom_fractional.extra.headless_screen.layout.cssVisualViewport.clientWidth=80,
  p=>p.headless_explicit_info.extra.headless_screen.effectiveCommandLine='chrome --headless --screen-info={1280x960 devicePixelRatio=2}',
  p=>p.headless_force_scale.extra.headless_screen.effectiveCommandLine+=' --screen-info={1280x960 devicePixelRatio=2}',
  p=>p.headless_native_ignored.extra.headless_screen.effectiveCommandLine+=' --uxr-device-pixel-ratio=2',
  p=>p.headless_off_ignored.extra.headless_screen.screenshot.png=''];
 for(const mutate of mutations){const f=fresh();mutate(f);assert.ok(failures(validateHeadlessScreens(f)).length);}
 assert.throws(()=>pngDimensions(''));assert.throws(()=>pngDimensions(Buffer.from('not a PNG').toString('base64')));
});
