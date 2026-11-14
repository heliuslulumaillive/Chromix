import test from 'node:test';
import assert from 'node:assert/strict';
import {validateFontAcceptance,WINDOWS_FONT_COHORT_ORACLE,WINDOWS_FONT_PACKAGE_ORACLE} from '../fingerprint_font_acceptance.mjs';

// Fixed observations for OS, language-pack and application font families.
// These fixtures test evidence rejection, not the C++ classification rule.
const families=['Arial','Segoe UI Light','Comic Sans MS','Ink Free','Meiryo','Agency FB'];
function probe(allowed){
  const entries=[...families,'DefinitelyAbsentFontXYZ'].map(family=>({family,
    ...(allowed.includes(family)?{loaded:true,status:'loaded'}:{loaded:false,error:{name:'NetworkError'}})}));
  const cssNodes=families.map((family,i)=>({family,nodeId:'font'+i,width:32,height:32}));
  const cssPlatformFonts=Object.fromEntries(cssNodes.map(row=>[row.nodeId,{fonts:[{
    familyName:allowed.includes(row.family)?row.family:'Arial',glyphCount:8}]}]));
  return {version:1,windowLocal:{supported:true,entries:structuredClone(entries)},workerLocal:{supported:true,entries:structuredClone(entries)},
    localFonts:{supported:true,records:allowed.map(family=>({family,postscriptName:family+'-Regular',fullName:family,style:'Regular'}))},
    cssNodes,cssPlatformFonts,ownedFont:{loaded:true,width:16,cssWidth:16}};
}
function records(){
  const core=['Arial','Segoe UI Light','Comic Sans MS','Ink Free'];
  return Object.fromEntries(Object.entries({all_native:families,
    fonts_shard40:core,fonts_shard41:[...core,'Meiryo'],
    fonts_shard42:[...core,'Agency FB'],fonts_shard43:families,
    fonts_allowlists:['Arial'],fonts_empty:[]}).map(([id,allowed])=>[id,{extra:{fonts:probe(allowed)}}]));
}
const validate=r=>validateFontAcceptance(r,WINDOWS_FONT_COHORT_ORACLE);
test('all four cohorts require actual cross-channel blocking and retention',()=>{
  const r=validate(records());assert.equal(r.positive_acceptance.shard_fonts.status,'observed_pass');
  assert.ok(r.checks.some(c=>c.name==='windowLocal:Segoe UI Light:retained'&&c.passed));
});
test('old random core deletion cannot pass the cohort contract',()=>{
  const r=records();r.fonts_shard40.extra.fonts=probe(['Arial']);
  assert.equal(validate(r).positive_acceptance.shard_fonts.status,'failed');
});
test('an entirely native no-op cannot pass the masking contract',()=>{
  const r=records();for(const id of ['fonts_shard40','fonts_shard41','fonts_shard42'])r[id].extra.fonts=probe(families);
  assert.equal(validate(r).positive_acceptance.shard_fonts.status,'failed');
});
test('CSS alias leaks and Worker divergence are failures',()=>{
  for(const channel of ['css','worker']){
    const r=records(),p=r.fonts_shard40.extra.fonts;
    if(channel==='css')p.cssPlatformFonts.font5.fonts[0].familyName='Agency FB';
    else p.workerLocal.entries.find(e=>e.family==='Agency FB').loaded=true;
    assert.equal(validate(r).positive_acceptance.shard_fonts.status,'failed');
  }
});
test('missing cohorts and changed native font metadata cannot pass',()=>{
  const r=records();delete r.fonts_shard43;
  assert.equal(validate(r).positive_acceptance.shard_fonts.status,'failed');
  const other=records();other.fonts_shard42.extra.fonts.localFonts.records[0].style='Synthetic';
  assert.equal(validate(other).positive_acceptance.shard_fonts.status,'failed');
});
test('no native-available optional inputs stays untested',()=>{
  const r=records();r.all_native.extra.fonts=probe(['Arial']);
  assert.equal(validate(r).positive_acceptance.shard_fonts.status,'not_tested');
});

test('package oracle covers all twelve installations and protects language fonts',()=>{
  const oracle=WINDOWS_FONT_PACKAGE_ORACLE;
  for(let index=0;index<12;index++){
    const row=oracle.shard['fonts_pack'+(120+index)];assert.ok(row);
    for(const name of ['Yu Gothic UI','Yu Gothic UI Bold','Segoe UI Variable Text','Meiryo','Batang','SimHei','Microsoft Uighur'])assert.equal(row[name],true);
    for(const name of ['Agency FB','Lucida Bright','MS Outlook','MT Extra'])assert.equal(row[name],row.Century);
    assert.equal(row['Noto Sans SC'],row['Noto Sans SC Black']);
    assert.equal(row['Noto Serif SC'],row['Noto Serif SC Bold']);
    assert.equal(row['Liberation Sans'],row['Liberation Serif']);
  }
  assert.equal(new Set(Array.from({length:12},(_,i)=>JSON.stringify(oracle.shard['fonts_pack'+(120+i)]))).size,12);
});

function packageProbe(allowed){
  const names=WINDOWS_FONT_PACKAGE_ORACLE.selected_families;
  const entries=[...names,'DefinitelyAbsentFontXYZ'].map(family=>({family,...(allowed.includes(family)?{loaded:true,status:'loaded'}:{loaded:false,error:{name:'NetworkError'}})}));
  const cssNodes=names.map((family,i)=>({family,nodeId:'packagefont'+i,width:32,height:32}));
  return {version:1,windowLocal:{supported:true,entries:structuredClone(entries)},workerLocal:{supported:true,entries:structuredClone(entries)},
    localFonts:{supported:true,records:allowed.map(family=>({family,postscriptName:family+'-Regular',fullName:family,style:'Regular'}))},
    cssNodes,cssPlatformFonts:Object.fromEntries(cssNodes.map(row=>[row.nodeId,{fonts:[{familyName:allowed.includes(row.family)?row.family:'Arial',glyphCount:8}]}])),
    ownedFont:{loaded:true,width:16,cssWidth:16}};
}
function packageRecords(){return {all_native:{extra:{fonts:packageProbe(WINDOWS_FONT_PACKAGE_ORACLE.selected_families)}},
  ...Object.fromEntries(Object.entries(WINDOWS_FONT_PACKAGE_ORACLE.shard).map(([id,row])=>[id,{extra:{fonts:packageProbe(Object.keys(row).filter(name=>row[name]))}}]))};}
test('twelve package scenes require real cross-channel installation evidence',()=>{
  assert.equal(validateFontAcceptance(packageRecords(),WINDOWS_FONT_PACKAGE_ORACLE).positive_acceptance.shard_fonts.status,'observed_pass');
  const missing=packageRecords();delete missing.fonts_pack131;
  assert.equal(validateFontAcceptance(missing,WINDOWS_FONT_PACKAGE_ORACLE).positive_acceptance.shard_fonts.status,'failed');
});
test('Office fragments, deleted OS aliases and no-op packages fail acceptance',()=>{
  for(const name of ['Century','Yu Gothic UI']){
    const r=packageRecords();r.fonts_pack121.extra.fonts.windowLocal.entries.find(e=>e.family===name).loaded=false;
    assert.equal(validateFontAcceptance(r,WINDOWS_FONT_PACKAGE_ORACLE).positive_acceptance.shard_fonts.status,'failed');
  }
  const r=packageRecords();for(const key of Object.keys(WINDOWS_FONT_PACKAGE_ORACLE.shard))r[key].extra.fonts=structuredClone(r.all_native.extra.fonts);
  assert.equal(validateFontAcceptance(r,WINDOWS_FONT_PACKAGE_ORACLE).positive_acceptance.shard_fonts.status,'failed');
});
