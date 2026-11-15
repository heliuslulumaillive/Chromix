// Launcher-side selection for the external-catalog kernel protocol. The kernel
// independently authenticates this immutable generation and every selected file.
import {createHash} from 'node:crypto';
import {lstat,readFile} from 'node:fs/promises';
import path from 'node:path';
import {assertOrdinaryPath,loadCurrentCatalog} from './font_catalog.mjs';

export async function prepareExternalCountryFontPlan({country,resourcesDir,catalogRoot=path.dirname(resourcesDir??''),key}){
  const requestedCountry=typeof country==='string'?country.trim().toUpperCase():'';
  const base={requestedCountry:requestedCountry||null,effectiveCountry:'US',group:'base',
    selectedFontFiles:0,keepNativeSystemFonts:true,args:[]};
  let catalog;
  try{catalog=await loadCurrentCatalog(catalogRoot,key);}
  catch{return {...base,fallbackReason:'catalog-unavailable'};}
  const diagnostics={catalogProtocol:2,catalogVersion:catalog.version,catalogSHA256:catalog.sha256,catalogKeyId:catalog.keyId};
  const group=/^[A-Z]{2}$/.test(requestedCountry)&&Object.hasOwn(catalog.countries,requestedCountry)?catalog.countries[requestedCountry]:null;
  if(!group)return {...base,...diagnostics,fallbackReason:'no-regional-supplement'};
  try{
    await assertOrdinaryPath(resourcesDir,true);
    const assets=catalog.groups[group];
    for(const asset of assets){
      const file=path.join(resourcesDir,asset.file);await assertOrdinaryPath(file);
      const info=await lstat(file);if(info.size!==asset.size)throw Error('Selected font resource size differs');
      const bytes=await readFile(file);
      if(bytes.length!==asset.size||createHash('sha256').update(bytes).digest('hex')!==asset.sha256)throw Error('Selected font resource hash differs');
    }
    return {...base,...diagnostics,effectiveCountry:requestedCountry,group,fallbackReason:null,
      selectedFontFiles:assets.filter(a=>a.kind==='font').length,
      args:[`--uxr-fonts-resources-dir=${resourcesDir}`,`--uxr-fonts-resource-group=${group}`,`--uxr-fonts-catalog-dir=${catalog.directory}`]};
  }catch{return {...base,...diagnostics,fallbackReason:'resources-unavailable'};}
}
