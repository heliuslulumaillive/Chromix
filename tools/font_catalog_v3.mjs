// Public-key-only runtime parser. Editing country/font data never changes this
// protocol module or the kernel. Input must be authenticated before parsing.
import {createHash,createPublicKey,verify} from 'node:crypto';
import {lstat,readFile} from 'node:fs/promises';
import path from 'node:path';

export const catalogProtocol=3;
export const signatureDomain=Buffer.from('ShardXFontCatalog-v3\0','ascii');
const magic=Buffer.from('SXFONT3\0','ascii');
const hex=/^[a-f0-9]{64}$/;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const requireValue=(condition,message)=>{if(!condition)throw Error(message);};

function publicKey(record){
  requireValue(record?.schemaVersion===2&&record.algorithm==='ECDSA-P256-SHA256-P1363'&&record.domain==='ShardXFontCatalog-v3','Unsupported catalog trust anchor');
  const blob=Buffer.from(record.cngPublicBlob??'','base64');
  requireValue(blob.length===72&&blob.readUInt32LE(0)===0x31534345&&blob.readUInt32LE(4)===32&&sha(blob)===record.keyId,'Invalid catalog public key');
  return createPublicKey({key:{kty:'EC',crv:'P-256',x:blob.subarray(8,40).toString('base64url'),y:blob.subarray(40,72).toString('base64url')},format:'jwk'});
}

export function verifyCatalogBytes(payload,signature,key){
  requireValue(Buffer.isBuffer(payload)&&payload.length>=18&&payload.length<=1024*1024,'Invalid catalog payload size');
  requireValue(Buffer.isBuffer(signature)&&signature.length===64,'Invalid catalog signature size');
  requireValue(verify('sha256',Buffer.concat([signatureDomain,payload]),{key:publicKey(key),dsaEncoding:'ieee-p1363'},signature),'Catalog signature rejected');
  let position=0;
  const take=count=>{requireValue(Number.isSafeInteger(count)&&count>=0&&count<=payload.length-position,'Truncated catalog');const value=payload.subarray(position,position+count);position+=count;return value;};
  const integer=count=>{const bytes=take(count);return count===1?bytes[0]:count===2?bytes.readUInt16LE():Number(bytes.readBigUInt64LE());};
  requireValue(take(8).equals(magic)&&integer(2)===3,'Unsupported catalog protocol');
  const assetCount=integer(2),groupCount=integer(2),countryCount=integer(2),versionLength=integer(2);
  requireValue(assetCount>0&&assetCount<=4096&&groupCount>0&&groupCount<=512&&countryCount>0&&countryCount<=512&&versionLength>0&&versionLength<=64,'Catalog limits exceeded');
  const versionBytes=take(versionLength);
  requireValue(versionBytes.every(b=>b>=33&&b<=126),'Invalid catalog version');
  const version=versionBytes.toString('ascii'),assets=[],identities=new Set();
  for(let i=0;i<assetCount;i++){
    const kind=integer(1),size=integer(8),sha256=take(32).toString('hex');
    requireValue(kind<=1&&Number.isSafeInteger(size)&&size>0&&size<128*1024*1024&&!identities.has(sha256),'Invalid or duplicate catalog asset');
    identities.add(sha256);assets.push({file:sha256+'.blob',sha256,size,kind:kind===1?'font':'license'});
  }
  const groups=Object.create(null),groupNames=[];
  for(let i=0;i<groupCount;i++){
    const length=integer(1);requireValue(length>0&&length<=96,'Invalid group name length');
    const nameBytes=take(length);requireValue(nameBytes.every(b=>b>=0x20&&b<0x7f),'Non-ASCII group name');
    const name=nameBytes.toString('ascii'),count=integer(2);
    requireValue(/^[a-z][a-z0-9-]*$/.test(name)&&!Object.hasOwn(groups,name)&&count>0&&count<=128,'Invalid or duplicate group');
    const selected=[],indexes=new Set();
    for(let j=0;j<count;j++){
      const index=integer(2);requireValue(index<assets.length&&!indexes.has(index),'Invalid or duplicate group asset');indexes.add(index);selected.push(assets[index]);
    }
    const fonts=selected.filter(a=>a.kind==='font').length;
    requireValue(fonts>0&&fonts<=64&&selected.some(a=>a.kind==='license')&&selected.reduce((n,a)=>n+a.size,0)<=512*1024*1024,'Incomplete or oversized group');
    groups[name]=Object.freeze(selected);groupNames.push(name);
  }
  const countries=Object.create(null);
  for(let i=0;i<countryCount;i++){
    const bytes=take(2),index=integer(2);
    requireValue(bytes.every(b=>b>=65&&b<=90)&&index<groupNames.length,'Invalid country mapping');
    const country=bytes.toString('ascii');requireValue(!Object.hasOwn(countries,country),'Duplicate country mapping');countries[country]=groupNames[index];
  }

  const sourceCount=integer(2);requireValue(sourceCount>0&&sourceCount<=4096,'Invalid native source count');
  requireValue(Object.hasOwn(countries,'US'),'Missing verified US fallback');
  const nativeSources=[],sourceIdentities=new Set();let commonCount=0;
  const sourceKinds=['core','regional','shared'];
  const safeName=bytes=>bytes.length>0&&bytes.length<=160&&bytes.every(b=>b<128)&&/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(bytes.toString('ascii'))&&!bytes.toString('ascii').includes('..');
  for(let i=0;i<sourceCount;i++){
    const kind=integer(1),fileLength=integer(1),fileBytes=take(fileLength),packageLength=integer(1),packageBytes=take(packageLength),size=integer(8),sha256=take(32).toString('hex');
    requireValue(kind<=2&&fileLength<=127&&safeName(fileBytes)&&packageLength<=160&&Number.isSafeInteger(size)&&size>0&&size<128*1024*1024,'Invalid native source');
    const file=fileBytes.toString('ascii'),packageName=packageBytes.toString('ascii');
    requireValue((kind===2)===(packageLength>0)&&(!packageLength||safeName(packageBytes)&&packageName.includes('_')),'Invalid shared source package');
    const identity=[packageName.toLowerCase(),file.toLowerCase()].join('|');requireValue(!sourceIdentities.has(identity),'Duplicate native source');sourceIdentities.add(identity);
    if(kind!==1)commonCount++;
    nativeSources.push(Object.freeze({kind:sourceKinds[kind],file,package:packageName,size,sha256}));
  }
  requireValue(commonCount>0&&nativeSources.some(s=>s.kind==='core'),'Missing required common sources');
  const nativeGroups=Object.create(null);
  for(const name of groupNames){
    const count=integer(2);requireValue(count>=commonCount&&count<=1024,'Invalid selected source count');
    const selected=[],indexes=new Set();
    for(let i=0;i<count;i++){const index=integer(2);requireValue(index<nativeSources.length&&!indexes.has(index),'Invalid or duplicate selected source');indexes.add(index);selected.push(nativeSources[index]);}
    requireValue(selected.filter(s=>s.kind!=='regional').length===commonCount&&selected.reduce((n,s)=>n+s.size,0)<=2*1024*1024*1024,'Incomplete or oversized common source view');
    nativeGroups[name]=Object.freeze(selected);
  }
  requireValue(position===payload.length,'Unexpected trailing catalog bytes');
  return Object.freeze({protocol:3,version,sha256:sha(payload),keyId:key.keyId,countries:Object.freeze(countries),groups:Object.freeze(groups),nativeGroups:Object.freeze(nativeGroups)});
}

export async function assertOrdinaryPath(file,directory=false){
  requireValue(path.isAbsolute(file)&&!(process.platform==='win32'&&!/^[A-Za-z]:[\\/]/.test(file)),'Catalog path must be an absolute local path');
  for(let current=file;;current=path.dirname(current)){
    const info=await lstat(current);
    requireValue(!info.isSymbolicLink()&&(current!==file||(directory?info.isDirectory():info.isFile())),'Catalog path redirects or has the wrong type');
    if(path.dirname(current)===current)break;
  }
}

async function boundedRead(file,limit){
  await assertOrdinaryPath(file);const info=await lstat(file);requireValue(info.size>0&&info.size<=limit,'Catalog file size exceeds its bound');
  const bytes=await readFile(file);requireValue(bytes.length>0&&bytes.length<=limit,'Catalog changed size');return bytes;
}

export async function loadCatalogGeneration(directory,key,expectedHash=path.basename(directory)){
  requireValue(hex.test(expectedHash)&&path.basename(directory)===expectedHash,'Invalid immutable catalog generation');
  await assertOrdinaryPath(directory,true);
  const [payload,signature]=await Promise.all([boundedRead(path.join(directory,'catalog.bin'),1024*1024),boundedRead(path.join(directory,'catalog.sig'),64)]);
  requireValue(sha(payload)===expectedHash,'Catalog generation identity differs');
  return {...verifyCatalogBytes(payload,signature,key),directory};
}

export async function loadCurrentCatalog(resourceRoot,key){
  await assertOrdinaryPath(resourceRoot,true);
  const pointer=JSON.parse((await boundedRead(path.join(resourceRoot,'current.json'),4096)).toString('utf8'));
  requireValue(pointer.schemaVersion===3&&hex.test(pointer.catalogSHA256),'Invalid current catalog pointer');
  return loadCatalogGeneration(path.join(resourceRoot,'catalogs',pointer.catalogSHA256),key,pointer.catalogSHA256);
}
