import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {verifyCatalogBytes} from '../font_catalog_v3.mjs';
const key=JSON.parse(await readFile(new URL('../../build/font-catalog-key.json',import.meta.url),'utf8'));
async function fixture(name){return [await readFile(new URL(`./fixtures/signed-font-catalog-v3/${name}.bin`,import.meta.url)),await readFile(new URL(`./fixtures/signed-font-catalog-v3/${name}.sig`,import.meta.url)),key];}
const fixtures=Object.fromEntries(await Promise.all(['unsafe-source','missing-core','trailing'].map(async n=>[n,await fixture(n)])));
test('same public parser accepts new countries, groups and source pins from signed data',async()=>{
 const a=verifyCatalogBytes(...await fixture('a')),b=verifyCatalogBytes(...await fixture('b'));
 assert.equal(a.protocol,3);assert.equal(a.countries.SG,undefined);assert.equal(b.countries.SG,'sample-b');
 assert.equal(a.nativeGroups['sample-a'].length,1);assert.equal(b.nativeGroups['sample-b'].length,2);
 assert.equal(b.nativeGroups['sample-b'][1].file,'times.ttf');
});
test('valid signatures cannot authorize path traversal, incomplete common sources or trailing data',async()=>{
 for(const name of ['unsafe-source','missing-core','trailing'])assert.throws(()=>verifyCatalogBytes(...fixtures[name]));
});
test('v2 data is rejected by the source-controlled protocol',async()=>{
 const file=new URL('./fixtures/signed-font-catalog-v2/b.bin',import.meta.url),sig=new URL('./fixtures/signed-font-catalog-v2/b.sig',import.meta.url);
 const payload=await readFile(file),signature=await readFile(sig);
 assert.throws(()=>verifyCatalogBytes(payload,signature,key));
});
