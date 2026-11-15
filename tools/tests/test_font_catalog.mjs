import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {verifyCatalogBytes} from '../font_catalog.mjs';
const key=JSON.parse(await readFile(new URL('../../build/font-catalog-key.json',import.meta.url),'utf8'));
const fixture=async name=>Promise.all(['bin','sig'].map(ext=>readFile(new URL('./fixtures/signed-font-catalog-v2/'+name+'.'+ext,import.meta.url))));
test('signed data independently adds a country, combination and actual font pin',async()=>{
  const a=verifyCatalogBytes(...await fixture('a'),key),b=verifyCatalogBytes(...await fixture('b'),key);
  assert.equal(a.countries.SG,undefined);assert.equal(b.countries.SG,'sample-b');
  assert.equal(a.groups['sample-a'].filter(f=>f.kind==='font').length,1);
  assert.equal(b.groups['sample-b'].filter(f=>f.kind==='font').length,2);
});
test('signature, bounds and complete payload consumption remain mandatory',async()=>{
  const [bytes,sig]=await fixture('b');
  for(const position of [0,8,bytes.length-1]){const bad=Buffer.from(bytes);bad[position]^=1;assert.throws(()=>verifyCatalogBytes(bad,sig,key),/signature rejected/);}
  const bad=Buffer.from(sig);bad[0]^=1;assert.throws(()=>verifyCatalogBytes(bytes,bad,key),/signature rejected/);
  assert.throws(()=>verifyCatalogBytes(bytes,sig.subarray(1),key),/signature size/);
  assert.throws(()=>verifyCatalogBytes(bytes,sig,{...key,keyId:'0'.repeat(64)}),/public key/);
  assert.throws(()=>verifyCatalogBytes(...[]),/payload size/);
  const trailing=await fixture('trailing');assert.throws(()=>verifyCatalogBytes(...trailing,key),/trailing catalog/);
});
