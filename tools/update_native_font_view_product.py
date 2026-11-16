"""Rebuild owned font topic and exact source identities from reviewed preimages."""
import difflib,hashlib,json,subprocess,tempfile
from pathlib import Path
from generate_private_font_packages import render
from generate_windows_font_packages import source_from_patch
ROOT=Path(__file__).resolve().parents[1]
DONOR='96173fd8dd4c883cd3201f2795860474b7effd52'
sha=lambda data:hashlib.sha256(data).hexdigest()
def section(text,name):
    start=text.index('diff --git a/'+name+' b/'+name+'\n');end=text.find('diff --git ',start+1)
    return start,len(text) if end<0 else end
manifest_path=ROOT/'build/lite-windows157.json';manifest=json.loads(manifest_path.read_text('utf8'));topic=ROOT/'patches/lite-windows157/13-fonts.patch'
donor_manifest=json.loads(subprocess.check_output(['git','show',DONOR+':build/lite-windows157.json'],cwd=ROOT))
name='ui/gfx/win/direct_write.cc';row=next(r for r in donor_manifest['source_files'] if r['path']==name)
old=(ROOT/'build/checkpoint-sources'/(row['after_sha256']+'.cc')).read_bytes();assert sha(old)==row['after_sha256']
text=old.decode('utf8');needle='''    std::shared_ptr<base::uxr_private_fonts_v1::Resources> resources;
    Microsoft::WRL::ComPtr<IDWriteFontCollection> fonts;'''
assert text.count(needle)==1
text=text.replace(needle,'''    std::shared_ptr<base::uxr_private_fonts_v1::Resources> resources;
    std::shared_ptr<base::uxr_private_fonts_v1::NativeViewSnapshot> native_snapshot;
    Microsoft::WRL::ComPtr<IDWriteFontCollection> fonts;''')
before='''      result = country_resources
          ? base::uxr_private_fonts_v1::VerifyCountry(root, group, &resources)
          : base::uxr_private_fonts_v1::Verify(root, &resources);
      if (SUCCEEDED(result)) result = base::uxr_private_fonts_v1::CreateCollection(
          factory, resources, &fonts);
      if (FAILED(result)) {
        LOG(ERROR) << "Private font resources rejected: " << result;
        // All font interfaces use the same native fallback for country mode.
        // Legacy explicit resource experiments retain their failure result.
        if (country_resources) result = S_FALSE;
      }'''
after='''      if (country_resources) {
        const HRESULT additions = base::uxr_private_fonts_v1::VerifyCountry(
            root, group, &resources);
        if (FAILED(additions)) {
          LOG(ERROR) << "Regional font additions unavailable: " << additions;
          resources.reset();
        }
        result = base::uxr_private_fonts_v1::CreateControlledCollection(
            factory, group == "hans-cn", resources, &native_snapshot, &fonts);
        if (SUCCEEDED(result) && native_snapshot->additions_rejected)
          LOG(ERROR) << "Regional font loading failed; controlled base retained";
        // Never expose the complete host collection after controlled selection
        // fails. Launcher preflight supplies a readable compatibility error;
        // every native font entry point shares this fail-closed boundary.
        CHECK(SUCCEEDED(result)) << "Controlled native font view rejected: "
                                 << result;
      } else {
        result = base::uxr_private_fonts_v1::Verify(root, &resources);
        if (SUCCEEDED(result)) result = base::uxr_private_fonts_v1::CreateCollection(
            factory, resources, &fonts);
        if (FAILED(result))
          LOG(ERROR) << "Private font resources rejected: " << result;
      }'''
assert text.count(before)==1;text=text.replace(before,after)
donor_topic=subprocess.check_output(['git','show',DONOR+':patches/lite-windows157/13-fonts.patch'],cwd=ROOT).decode('utf8');start,end=section(donor_topic,name)
cached=Path(r'G:\Shardxlite\cache\upstream-source-objects')/(row['before_sha256']+'.blob')
original=cached.read_bytes();assert sha(original)==row['before_sha256']
with tempfile.TemporaryDirectory(prefix='native-view-source-') as temporary:
    temp=Path(temporary);dest=temp/name;dest.parent.mkdir(parents=True);dest.write_bytes(original);patch=temp/'ui.patch';patch.write_text(donor_topic[start:end],encoding='utf8',newline='\n')
    subprocess.run(['git','-c','core.autocrlf=false','apply',str(patch)],cwd=temp,check=True,capture_output=True)
    assert dest.read_bytes()==old,'Reviewed old font patch does not reproduce the source donor'
new_section='diff --git a/'+name+' b/'+name+'\n'+''.join(difflib.unified_diff(original.decode('utf8').splitlines(keepends=True),text.splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name,n=3))
current=topic.read_text('utf8');start,end=section(current,name);current=current[:start]+new_section+current[end:];topic.write_text(current,encoding='utf8',newline='\n')
updates=[]
for name,content in [('ui/gfx/win/direct_write.cc',text.encode()),('base/uxr_private_font_packages_win.h',render().encode())]:
    entry=next(r for r in manifest['source_files'] if r['path']==name);prior=next(r['after_sha256'] for r in donor_manifest['source_files'] if r['path']==name);new_sha=sha(content);entry['after_sha256']=new_sha
    ext=Path(name).suffix;replacement='build/checkpoint-sources/'+new_sha+ext;path=ROOT/replacement
    if path.exists():assert path.read_bytes()==content
    else:path.write_bytes(content)
    updates.append({'path':name,'before_sha256':prior,'after_sha256':new_sha,'replacement_path':replacement})
for patch in manifest['patches']:patch['sha256']=sha((ROOT/patch['path']).read_bytes())
manifest['runtime']['country_font_resources']['manifest_sha256']=sha((ROOT/'build/country-font-resources.json').read_bytes())
manifest['runtime']['native_font_view']={'version':1,'manifest':'build/native-font-view.json','manifest_sha256':sha((ROOT/'build/native-font-view.json').read_bytes()),'core_source':'verified native system files','registered_inbox_shared_fonts':True,'hans_country':'CN','fallback':'verified common view only; required-source failure rejects startup','install_to_windows':False}
payload={k:v for k,v in manifest.items() if k!='stack_sha256'};manifest['stack_sha256']=sha(json.dumps(payload,sort_keys=True,separators=(',',':')).encode());manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
(ROOT/'build/native-font-view-source-updates.json').write_text(json.dumps({'donor':'96173fd','source_run':'43767199807','source_stage':1,'updates':updates},indent=2)+'\n')
print(json.dumps({'sourceUpdates':updates,'stackSHA256':manifest['stack_sha256']}))
