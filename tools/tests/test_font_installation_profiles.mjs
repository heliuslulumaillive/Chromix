import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import path from 'node:path';import {createHash} from 'node:crypto';
import {digest,fontInventoryDigest,visibleNamesDigest,systemFontId,fontStyleId,admitProfile,verifiedPool,fixedSelection} from '../font_installation_profiles.mjs';
const DLL='d'.repeat(64),fontHash='f'.repeat(64);
// Synthetic evidence tests admission rejection only. No fixture is a real
// installation and none is added to the shipped country registry.
async function fixture(t,id='jp-one'){
 const root=await mkdtemp(path.join(tmpdir(),'complete-font-profile-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const environment={product:'Windows 11',release:'23H2',build:'22631',ubr:5189,updates:['KB5055528'],installLanguage:'ja-JP',systemUILanguage:'ja-JP',languageComponentsComplete:true,languageComponents:[{id:'Language.Fonts.Jpan',installed:true}],applicationsComplete:true,applications:[{product:'Office',installed:false}]};
 const fonts=['Yu Gothic UI','Malgun Gothic','Noto Sans JP'].map(family=>({family,fullName:family+' Regular',postscriptName:family.replaceAll(' ','')+'-Regular',style:'Regular',weight:400,stretch:5,fileSHA256:fontHash,origin:'windows-system'}));
 for(const f of fonts)f.aliases=[f.family,f.fullName,f.postscriptName];
 const configuration={visibilityMode:'native-complete',fontInventorySHA256:fontInventoryDigest(fonts)};
 const profile={id,country:'JP',status:'verified',defaultEligible:true,environment,fonts,configuration,dllSHA256:DLL,evidence:{}};
 async function pin(name,value){const text=JSON.stringify(value);await writeFile(path.join(root,name+'.json'),text);return {path:name+'.json',sha256:createHash('sha256').update(text).digest('hex')};}
 const systemFontIds=fonts.map(systemFontId);
 const native={status:'passed',dllSHA256:DLL,visibleInventorySHA256:fontInventoryDigest(fonts),visibleNamesSHA256:visibleNamesDigest(fonts),renderedOutputSHA256:'a'.repeat(64),systemFontIds,channels:Object.fromEntries(['window','worker','iframe','fontAccess','css'].map(c=>[c,{baselineSystemFontIds:systemFontIds,actualSystemFontIds:systemFontIds,baselineStyles:fonts.map(fontStyleId),actualStyles:fonts.map(fontStyleId)}]))};
 profile.evidence.installation=await pin('installation',{kind:'real-installation-capture',complete:true,environment,fonts});
 profile.evidence.nativeValidation=await pin('native',native);
 profile.evidence.stability=await pin('stability',{dllSHA256:DLL,profileId:id,savedConfigurationSHA256:digest(configuration),reopenedConfigurationSHA256:digest(configuration),firstVisibleInventorySHA256:fontInventoryDigest(fonts),reopenedVisibleInventorySHA256:fontInventoryDigest(fonts)});
 const web={dllSHA256:DLL,profileId:id,configurationSHA256:digest(configuration),visibleInventorySHA256:fontInventoryDigest(fonts),collectionMethod:'automation',observations:[{eventId:'synthetic-event',rareDevice:false,bucket:'<p95'}]};
 profile.evidence.web=await pin('web',web);
 return {root,profile,native,web,pin,options:{evidenceRoot:root,expectedDLLSHA256:DLL},hostEnvironment:environment,hostInventorySHA256:fontInventoryDigest(fonts)};
}
test('missing country evidence never falls back to engineered font combinations',async t=>{
 const f=await fixture(t);const registry={schema_version:3,profiles:[]};
 await assert.rejects(fixedSelection({directory:path.join(f.root,'profile'),country:'KR',seed:'42',registry,...f.options,...f}),/No verified compatible/);
});
test('a complete captured installation retains shared language fonts',async t=>{
 const f=await fixture(t);const p=await admitProfile(f.profile,f.options);
 assert.ok(p.fonts.some(r=>r.family==='Yu Gothic UI'));assert.ok(p.fonts.some(r=>r.family==='Malgun Gothic'));assert.ok(p.fonts.some(r=>r.family==='Noto Sans JP'));
});
test('catalogues, absent Office version and mismatched actual DLL fail closed',async t=>{
 const f=await fixture(t);const bad=structuredClone(f.profile);bad.evidence.installation=await f.pin('catalogue',{kind:'documentation-catalogue',complete:true,environment:f.profile.environment,fonts:f.profile.fonts});
 await assert.rejects(admitProfile(bad,f.options),/not an installation/);
 const version=structuredClone(f.profile);version.environment.applications=[{product:'Office',installed:true}];await assert.rejects(admitProfile(version,f.options),/application state/);
 await assert.rejects(admitProfile(f.profile,{...f.options,expectedDLLSHA256:'e'.repeat(64)}),/actual candidate DLL/);
});
test('all interfaces, styles and JP/KR-related system families must survive',async t=>{
 const f=await fixture(t);
 for(const channel of ['window','worker','iframe','fontAccess','css']){
  const broken=structuredClone(f.native);broken.channels[channel].actualSystemFontIds=broken.systemFontIds.slice(0,2);const p=structuredClone(f.profile);p.evidence.nativeValidation=await f.pin('missing-'+channel,broken);await assert.rejects(admitProfile(p,f.options),/System font preservation/);
 }
 const broken=structuredClone(f.native);broken.channels.css.actualStyles=['regular400'];const p=structuredClone(f.profile);p.evidence.nativeValidation=await f.pin('missing-weight',broken);await assert.rejects(admitProfile(p,f.options),/weight\/style\/alias/);
});
test('tampered receipts and experimental profiles cannot enter defaults',async t=>{
 const f=await fixture(t);await writeFile(path.join(f.root,'web.json'),'{}');await assert.rejects(admitProfile(f.profile,f.options),/Evidence identity changed/);
 await assert.rejects(admitProfile({...f.profile,status:'experimental'},f.options),/Experimental/);
});

test('matching but incomplete style receipts cannot omit captured aliases or weights',async t=>{
 const f=await fixture(t),native=structuredClone(f.native);
 native.channels.window.baselineStyles=native.channels.window.actualStyles=native.channels.window.actualStyles.slice(0,1);
 const p=structuredClone(f.profile);p.evidence.nativeValidation=await f.pin('omitted-style',native);
 await assert.rejects(admitProfile(p,f.options),/weight\/style\/alias/);
 const aliases=structuredClone(f.profile);aliases.fonts[0].aliases=[aliases.fonts[0].family];
 await assert.rejects(admitProfile(aliases,f.options),/aliases/);
});

test('Rare receipts for another configuration or inventory cannot be reused',async t=>{
 const f=await fixture(t);
 for(const key of ['profileId','configurationSHA256','visibleInventorySHA256']){
  const p=structuredClone(f.profile);p.evidence.web=await f.pin('other-'+key,{...f.web,[key]:key==='profileId'?'other-profile':'e'.repeat(64)});
  await assert.rejects(admitProfile(p,f.options),/Rare observations/);
 }
});
test('selection persists after exit-country changes and rejects silent reselection',async t=>{
 const f=await fixture(t);const registry={schema_version:3,profiles:[f.profile]};const args={directory:path.join(f.root,'saved'),country:'JP',seed:'42',registry,...f.options,hostEnvironment:f.hostEnvironment,hostInventorySHA256:f.hostInventorySHA256};
 const first=await fixedSelection(args);const again=await fixedSelection({...args,country:'KR',exitCountry:'US'});assert.deepEqual(again,first);
 await assert.rejects(fixedSelection({...args,seed:'43'}),/explicit recreation/);
 assert.deepEqual(JSON.parse(await readFile(path.join(args.directory,'font-installation-selection.json'),'utf8')),first);
 await writeFile(path.join(args.directory,'font-installation-selection.json'),JSON.stringify({...first,configuration:{...first.configuration,visibilityMode:'mask-some-fonts'}}));
 await assert.rejects(fixedSelection(args),/explicit recreation/);
});
test('identical actual visible outputs have one random-selection slot',async t=>{
 const f=await fixture(t);const two=structuredClone(f.profile);two.id='jp-two';two.evidence.stability=await f.pin('stability-two',{dllSHA256:DLL,profileId:two.id,savedConfigurationSHA256:digest(two.configuration),reopenedConfigurationSHA256:digest(two.configuration),firstVisibleInventorySHA256:fontInventoryDigest(two.fonts),reopenedVisibleInventorySHA256:fontInventoryDigest(two.fonts)});
 two.evidence.web=await f.pin('web-two',{...f.web,profileId:two.id});
 const result=await verifiedPool({schema_version:3,profiles:[f.profile,two]},{country:'JP',...f.options,hostEnvironment:f.hostEnvironment,hostInventorySHA256:f.hostInventorySHA256});assert.equal(result.profiles.length,1);
});
