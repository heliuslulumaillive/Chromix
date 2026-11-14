// Launcher-side complete-installation admission and immutable profile choice.
// Country and seed never create a font list; they select an evidenced record.
import {createHash} from 'node:crypto';
import {readFile,lstat,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const countries=new Set(['JP','KR','US']),hex=/^[a-f0-9]{64}$/;
const channels=['window','worker','iframe','fontAccess','css'];
const stable=value=>value===null||typeof value!=='object'?JSON.stringify(value):Array.isArray(value)?'['+value.map(stable).join(',')+']':'{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+stable(value[k])).join(',')+'}';
export const digest=value=>createHash('sha256').update(stable(value)).digest('hex');
export function fontInventoryDigest(fonts){
 if(!Array.isArray(fonts)||!fonts.length)throw Error('Missing actual complete font inventory');
 return digest([...fonts].sort((a,b)=>stable(a).localeCompare(stable(b),'en')));
}
export function visibleNamesDigest(fonts){return digest(fonts.map(({family,fullName,postscriptName,style})=>({family,fullName,postscriptName,style})).sort((a,b)=>stable(a).localeCompare(stable(b),'en')));}
export const systemFontId=font=>font.fileSHA256+':'+font.postscriptName;
export const fontStyleId=font=>digest({id:systemFontId(font),family:font.family,fullName:font.fullName,postscriptName:font.postscriptName,style:font.style,weight:font.weight,stretch:font.stretch,aliases:[...new Set(font.aliases)].sort()});
function validateEnvironment(e){
 if(!e||!['Windows 10','Windows 11'].includes(e.product)||typeof e.release!=='string'||!/^\d{5}$/.test(e.build)||!Number.isInteger(e.ubr)||e.ubr<0||!Array.isArray(e.updates)||!e.updates.every(k=>/^KB\d+$/.test(k)))throw Error('Exact Windows build/update evidence required');
 if(typeof e.installLanguage!=='string'||typeof e.systemUILanguage!=='string'||!Array.isArray(e.languageComponents)||!e.languageComponentsComplete)throw Error('System language and complete component state required');
 if(!Array.isArray(e.applications)||!e.applicationsComplete||!e.applications.some(a=>a.product==='Office'&&typeof a.installed==='boolean'&&(a.installed===false||typeof a.version==='string'&&a.version.length>0)))throw Error('Versioned actual application state, including Office present/absent evidence, required');
}
function validateFonts(fonts){
 if(!Array.isArray(fonts)||!fonts.length)throw Error('Actual enumerated fonts required');
 for(const f of fonts)if(!f||!['family','fullName','postscriptName','style'].every(k=>typeof f[k]==='string'&&f[k].length>0)||!hex.test(f.fileSHA256??'')||!Number.isInteger(f.weight)||f.weight<1||f.weight>1000||!Number.isInteger(f.stretch)||f.stretch<1||f.stretch>9||!Array.isArray(f.aliases)||!f.aliases.every(a=>typeof a==='string'&&a.length>0)||![f.family,f.fullName,f.postscriptName].every(a=>f.aliases.includes(a))||!['windows-system','language-component','application-visible','application-private','unattributed'].includes(f.origin))throw Error('Font records need native names, aliases, weights, styles, backing identity and explicit origin');
}
async function evidence(root,ref){
 if(!ref||typeof ref.path!=='string'||!hex.test(ref.sha256??'')||path.isAbsolute(ref.path)||ref.path.split(/[\\/]/).some(p=>p==='..'||p==='')||ref.path.includes(':'))throw Error('Pinned relative evidence reference required');
 const base=path.resolve(root),file=path.resolve(base,ref.path);if(!file.startsWith(base+path.sep))throw Error('Evidence escapes its root');
 if((await lstat(base)).isSymbolicLink())throw Error('Evidence root redirects');
 for(let p=file;p!==base;p=path.dirname(p)){const info=await lstat(p);if(info.isSymbolicLink())throw Error('Redirected evidence is not admissible');}
 const bytes=await readFile(file);if(createHash('sha256').update(bytes).digest('hex')!==ref.sha256)throw Error('Evidence identity changed');return JSON.parse(bytes);
}
export async function admitProfile(profile,{evidenceRoot,expectedDLLSHA256}){
 if(!profile||profile.status!=='verified'||profile.defaultEligible!==true||!countries.has(profile.country)||typeof profile.id!=='string')throw Error('Experimental or unassigned configurations cannot enter a country default pool');
 validateEnvironment(profile.environment);validateFonts(profile.fonts);
 if(!hex.test(expectedDLLSHA256??'')||profile.dllSHA256!==expectedDLLSHA256)throw Error('Profile has no matching actual candidate DLL acceptance');
 const source=await evidence(evidenceRoot,profile.evidence?.installation);
 if(!['real-installation-capture','serviced-offline-installation-capture'].includes(source.kind)||source.complete!==true||digest(source.environment)!==digest(profile.environment)||fontInventoryDigest(source.fonts)!==fontInventoryDigest(profile.fonts))throw Error('Complete installation source differs; a catalogue or package union is not an installation');
 const native=await evidence(evidenceRoot,profile.evidence?.nativeValidation);
 if(native.dllSHA256!==expectedDLLSHA256||native.status!=='passed'||native.visibleInventorySHA256!==fontInventoryDigest(profile.fonts)||!Array.isArray(native.systemFontIds)||!native.systemFontIds.length)throw Error('Actual candidate native evidence is missing or incomplete');
 const declaredSystem=[...new Set(profile.fonts.filter(f=>['windows-system','language-component'].includes(f.origin)).map(systemFontId))].sort();
 const declaredStyles=[...new Set(profile.fonts.filter(f=>['windows-system','language-component'].includes(f.origin)).map(fontStyleId))].sort();
 if(digest([...new Set(native.systemFontIds)].sort())!==digest(declaredSystem))throw Error('Native system-font scope omits part of the captured installation');
 for(const channel of channels){
  const r=native.channels?.[channel];if(!r||!Array.isArray(r.baselineSystemFontIds)||!Array.isArray(r.actualSystemFontIds)||!r.baselineSystemFontIds.length||native.systemFontIds.some(id=>!r.baselineSystemFontIds.includes(id)||!r.actualSystemFontIds.includes(id)))throw Error('System font preservation missing in '+channel);
  if(!Array.isArray(r.baselineStyles)||!Array.isArray(r.actualStyles)||digest([...new Set(r.baselineStyles)].sort())!==digest(declaredStyles)||declaredStyles.some(style=>!r.actualStyles.includes(style)))throw Error('Family/weight/style/alias preservation missing in '+channel);
 }
 const stability=await evidence(evidenceRoot,profile.evidence?.stability);
 if(stability.dllSHA256!==expectedDLLSHA256||stability.profileId!==profile.id||stability.savedConfigurationSHA256!==digest(profile.configuration)||stability.reopenedConfigurationSHA256!==digest(profile.configuration)||stability.firstVisibleInventorySHA256!==fontInventoryDigest(profile.fonts)||stability.reopenedVisibleInventorySHA256!==fontInventoryDigest(profile.fonts))throw Error('Save/reopen identity stability not established');
 const web=await evidence(evidenceRoot,profile.evidence?.web);
 if(web.dllSHA256!==expectedDLLSHA256||web.profileId!==profile.id||web.configurationSHA256!==digest(profile.configuration)||web.visibleInventorySHA256!==fontInventoryDigest(profile.fonts)||!['automation','native-window'].includes(web.collectionMethod)||!Array.isArray(web.observations)||!web.observations.length||web.observations.some(o=>o.rareDevice!==false||typeof o.eventId!=='string'||!o.eventId.length||!['<p95','p95-p99'].includes(o.bucket)))throw Error('No eligible Rare observations for this complete configuration');
 if(profile.configuration?.visibilityMode!=='native-complete'||profile.configuration?.fontInventorySHA256!==fontInventoryDigest(profile.fonts)||Object.keys(profile.configuration).some(k=>!['visibilityMode','fontInventorySHA256'].includes(k)))throw Error('Only an evidenced complete visible configuration can be selected; per-font overrides are not allowed');
 if(native.visibleNamesSHA256!==visibleNamesDigest(profile.fonts)||typeof native.renderedOutputSHA256!=='string'||!hex.test(native.renderedOutputSHA256))throw Error('Actual visible names and rendered output identity required');
 return {...profile,configurationSHA256:digest(profile.configuration),visibleInventorySHA256:fontInventoryDigest(profile.fonts),visibleOutputSHA256:digest([native.visibleNamesSHA256,native.renderedOutputSHA256])};
}
export async function verifiedPool(registry,{country,evidenceRoot,expectedDLLSHA256,hostEnvironment,hostInventorySHA256}){
 if(registry?.schema_version!==3||!countries.has(country))throw Error('Explicit JP/KR/US country selection required');
 const accepted=[],excluded=[];
 for(const p of registry.profiles??[]){if(p.country!==country)continue;try{
  const entry=await admitProfile(p,{evidenceRoot,expectedDLLSHA256});
  if(digest(entry.environment)!==digest(hostEnvironment)||entry.visibleInventorySHA256!==hostInventorySHA256)throw Error('Host is not the evidenced complete installation');
  accepted.push(entry);
 }catch(e){excluded.push({id:p.id,reason:e.message});}}
 // Preserve every provenance record, but do not manufacture diversity by
 // giving identical visible inventories multiple random-selection slots.
 const unique=new Map();for(const p of accepted.sort((a,b)=>a.id.localeCompare(b.id)))if(!unique.has(p.visibleOutputSHA256))unique.set(p.visibleOutputSHA256,p);
 return {profiles:[...unique.values()],excluded};
}
export async function fixedSelection({directory,country,seed,registry,evidenceRoot,expectedDLLSHA256,hostEnvironment,hostInventorySHA256}){
 if(typeof seed!=='string'||!/^\d{1,20}$/.test(seed)||BigInt(seed)<1n||BigInt(seed)>0xffffffffffffffffn)throw Error('Valid stable unsigned-64 identity seed required');
 await mkdir(directory,{recursive:true});const file=path.join(directory,'font-installation-selection.json');
 let saved;try{if((await lstat(file)).isSymbolicLink())throw Error('Saved font configuration redirects');saved=JSON.parse(await readFile(file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const {profiles}=await verifiedPool(registry,{country:saved?.country??country,evidenceRoot,expectedDLLSHA256,hostEnvironment,hostInventorySHA256});
 if(saved){
  const current=profiles.find(p=>p.id===saved.profileId&&p.configurationSHA256===saved.configurationSHA256);
  if(!current||saved.schema_version!==3||saved.seed!==seed||saved.dllSHA256!==expectedDLLSHA256||saved.visibleInventorySHA256!==current.visibleInventorySHA256||digest(saved.configuration)!==current.configurationSHA256)throw Error('Saved font configuration no longer matches its acceptance; explicit recreation required');
  return saved; // Proxy/exit country is deliberately not a selection input.
 }
 if(!profiles.length)throw Error('No verified compatible complete Windows font installation for '+country);
 const pick=createHash('sha256').update('complete-font-installation-v3\0'+country+'\0'+seed).digest().readUInt32LE(0)%profiles.length,p=profiles[pick];
 const choice={schema_version:3,country,seed,profileId:p.id,configurationSHA256:p.configurationSHA256,visibleInventorySHA256:p.visibleInventorySHA256,dllSHA256:expectedDLLSHA256,configuration:p.configuration};
 await writeFile(file,JSON.stringify(choice,null,2)+'\n',{flag:'wx'});return choice;
}
