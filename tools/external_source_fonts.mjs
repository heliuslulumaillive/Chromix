// The signed data selects sources. Native code independently hashes and locks
// those sources before exposing the process-private DirectWrite collection.
import {createHash} from 'node:crypto';
import {readFile,lstat,stat} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import {assertOrdinaryPath,loadCurrentCatalog} from './font_catalog_v3.mjs';
const execute=promisify(execFile),verified=new Map();
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');

async function checkedFile(file,pin){
  const info=await lstat(file,{bigint:true});
  if(!info.isFile()||info.isSymbolicLink()||info.size!==BigInt(pin.size))throw Error('字体来源路径或大小不匹配');
  const identity=[file,info.dev,info.ino,info.size,info.mtimeNs,info.ctimeNs,pin.sha256].join('|');
  if(!verified.has(identity)){
    const bytes=await readFile(file);
    if(bytes.length!==pin.size||digest(bytes)!==pin.sha256)throw Error('字体文件版本或哈希不匹配');
    if(verified.size>=4096)verified.clear();verified.set(identity,true);
  }
}

async function registeredPackages(windowsRoot,families){
  if(!families.length)return [];
  if(families.some(f=>! /^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$/.test(f)))throw Error('共享字体包系列名无效');
  const names=families.map(f=>"'"+f+"'").join(',');
  const command="$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @(@("+names+") | ForEach-Object {Get-AppxPackage -Name $_} | Select-Object PackageFullName,InstallLocation) | ConvertTo-Json -Compress";
  const {stdout}=await execute(path.join(windowsRoot,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,timeout:20000,maxBuffer:32768,encoding:'utf8'});
  const raw=stdout.replace(/^\uFEFF/,'').trim(),value=raw?JSON.parse(raw):[];
  return value==null?[]:Array.isArray(value)?value:[value];
}

export async function preflightSourceSelection(sources,{windowsRoot=process.env.SystemRoot,discoverPackages=registeredPackages}={}){
  if(process.platform!=='win32'||typeof windowsRoot!=='string'||! /^[A-Za-z]:[\\/]/.test(windowsRoot))throw Error('标准字体视图需要已验证的 Windows 字体来源');
  const system=path.join(windowsRoot,'Fonts');await assertOrdinaryPath(system,true);
  const required=sources.filter(s=>s.kind!=='shared'),shared=sources.filter(s=>s.kind==='shared');
  for(const pin of required){
    try{await checkedFile(path.join(system,pin.file),pin);}
    catch(error){throw Error('标准字体基础或地区来源不匹配，已阻止启动：'+pin.file,{cause:error});}
  }
  const families=[...new Set(shared.map(s=>s.package.split('_')[0]))];
  const registered=await discoverPackages(windowsRoot,families);
  if(!Array.isArray(registered)||registered.length>64)throw Error('共享字体注册信息无效');
  let sharedFiles=0;
  for(const entry of registered){
    const selected=shared.filter(s=>s.package===entry.PackageFullName);
    if(!selected.length||!path.isAbsolute(entry.InstallLocation??''))throw Error('共享字体包版本尚未验证，已阻止标准模式启动');
    await assertOrdinaryPath(entry.InstallLocation,true);
    for(const pin of selected){await checkedFile(path.join(entry.InstallLocation,pin.file),pin);sharedFiles++;}
  }
  return {kind:'signed-source-controlled-font-view-v3',coreFiles:sources.filter(s=>s.kind==='core').length,
    regionalNativeFiles:sources.filter(s=>s.kind==='regional').length,registeredInboxSharedFiles:sharedFiles,keepNativeSystemFonts:false};
}

export async function prepareExternalSourceFontPlan({country,resourcesDir,catalogRoot=path.dirname(resourcesDir??''),key,preflight=preflightSourceSelection}){
  let catalog;
  try{catalog=await loadCurrentCatalog(catalogRoot,key);}
  catch(error){throw Error('标准字体签名清单验证失败，已阻止启动',{cause:error});}
  const requestedCountry=typeof country==='string'?country.trim().toUpperCase():'';
  const listed=/^[A-Z]{2}$/.test(requestedCountry)&&Object.hasOwn(catalog.countries,requestedCountry);
  const effectiveCountry=listed?requestedCountry:'US',group=catalog.countries[effectiveCountry];
  if(!group||!catalog.nativeGroups[group])throw Error('已验证美国字体配置不可用，已阻止启动');
  const view=await preflight(catalog.nativeGroups[group]);
  const root=typeof resourcesDir==='string'&&/^[A-Za-z]:[\\/]/.test(resourcesDir)?resourcesDir:'';
  let selectedFontFiles=0,fallbackReason=listed?null:'country-not-listed-use-US';
  try{
    if(!root)throw Error('Missing managed additions');await assertOrdinaryPath(root,true);
    for(const asset of catalog.groups[group])await checkedFile(path.join(root,asset.file),asset);
    selectedFontFiles=catalog.groups[group].filter(a=>a.kind==='font').length;
  }catch{fallbackReason='regional-additions-unavailable-controlled-base-retained';}
  return {requestedCountry:requestedCountry||null,effectiveCountry,group,...view,selectedFontFiles,fallbackReason,
    catalogProtocol:3,catalogVersion:catalog.version,catalogSHA256:catalog.sha256,catalogKeyId:catalog.keyId,
    args:[`--uxr-fonts-resources-dir=${root}`,`--uxr-fonts-resource-group=${group}`,`--uxr-fonts-catalog-dir=${catalog.directory}`]};
}
