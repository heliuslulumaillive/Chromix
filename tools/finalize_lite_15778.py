"""Freeze the reviewed .78 merge and generic external-catalog loader into topics."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from generate_private_font_packages import render
from compact_lite_patches import diff
from build_google_services_patch import search_engines_revision_from_deps
from google_services_inputs import write_if_changed


def main(work):
    before,after=work/'new/baseline',work/'new/restored'
    write_if_changed(after/'base/uxr_private_font_packages_win.h',render().encode())
    file=after/'ui/gfx/win/direct_write.cc';text=file.read_text('utf-8')
    replacements=[
      ('  const bool country_resources = base::CommandLine::ForCurrentProcess()->HasSwitch(\n      "uxr-fonts-resource-group");',
       '  const bool country_resources = base::CommandLine::ForCurrentProcess()->HasSwitch(\n      "uxr-fonts-resource-group");\n  const auto catalog = base::CommandLine::ForCurrentProcess()->GetSwitchValuePath(\n      "uxr-fonts-catalog-dir");'),
      ('const std::string& group, bool country_resources) {','const std::string& group, bool country_resources,\n                            const std::wstring& catalog) {'),
      ('base::uxr_private_fonts_v1::VerifyCountry(root, group, &resources)','base::uxr_private_fonts_v1::VerifyCountry(root, group, catalog, &resources)'),
      ('factory, root.value(), group, country_resources);','factory, root.value(), group, country_resources, catalog.value());')]
    for old,new in replacements:
        if new in text:continue
        if text.count(old)!=1:raise ValueError('DirectWrite call site changed: '+old)
        text=text.replace(old,new)
    write_if_changed(file,text.encode())
    baseline=json.loads(subprocess.check_output(['git','-C',str(ROOT),'show','96173fd8dd4c883cd3201f2795860474b7effd52:build/lite-windows157.json']))
    data=json.loads(json.dumps(baseline));sha=lambda b:hashlib.sha256(b).hexdigest()
    for entry in data['patches']:
        parts=[];names=[]
        for name in entry['files']:
            a=(before/name).read_bytes().replace(b'\r\n',b'\n') if (before/name).is_file() else None
            b=(after/name).read_bytes().replace(b'\r\n',b'\n') if (after/name).is_file() else None
            body=diff(name,a,b,git=True)
            if body:parts.append(body);names.append(name)
        raw=b''.join(parts);write_if_changed(ROOT/entry['path'],raw);entry.update(files=names,sha256=sha(raw))
    for row in data['source_files']:
        for tree,key in [(before,'before_sha256'),(after,'after_sha256')]:
            file=tree/row['path'];row[key]=sha(file.read_bytes().replace(b'\r\n',b'\n')) if file.is_file() else None
    runtime=data['runtime'];runtime.update(chromium_version='157.0.8103.78',core_commit='8c886dffc6ade39d8f7df5d64ace5a8824b455e1',windows_commit='20e9a348fc616bd73a366fbd5d8881613d6c5cb0')
    runtime['country_font_resources']={'version':2,'protocol':'signed-external-catalog-v2','trusted_key':'build/font-catalog-key.json','trusted_key_sha256':sha((ROOT/'build/font-catalog-key.json').read_bytes()),'install_to_windows':False,'source_gate_required':True,'fallback':'native-complete-no-additional-assets'}
    identity=json.loads((work.parent/'upstream-identity.json').read_bytes())
    identity['search_engines_commit']=search_engines_revision_from_deps((work.parent/'DEPS').read_bytes())
    preparation=json.loads((work/'preparation.json').read_bytes())
    source_identity=sha(json.dumps(preparation['versions']['new']['sourceInputs'],sort_keys=True,separators=(',',':')).encode())
    data['upstream_port']={k:identity[k] for k in ['chromium_commit','v8_commit','webrtc_commit','search_engines_commit']}
    data['upstream_port'].update(source_manifest_sha256=source_identity,review='build/windows157-port.json')
    data['legacy_equivalence'].update(result='ported-to-reviewed15778-source',baseline_version='157.0.8103.38',baseline_stack_sha256=baseline['stack_sha256'])
    data.pop('stack_sha256');data['stack_sha256']=sha(json.dumps(data,sort_keys=True,separators=(',',':')).encode())
    write_if_changed(ROOT/'build/lite-windows157.json',(json.dumps(data,indent=2)+'\n').encode())
    review={'schema_version':1,'status':'selected-source-replay-reviewed-before-build','source_version':'157.0.8103.38','target_version':'157.0.8103.78',
      'upstream_identity':identity,'source_manifest_sha256':source_identity,'upstream_run':43476895250,'upstream_artifact':14150905341,
      'old_product_checked_files':len(baseline['source_files']),'target_source_inputs':len(preparation['versions']['new']['sourceInputs']),
      'upstream_changed_files':json.loads((work/'merge-report.json').read_bytes())['upstreamChangedFiles'],'conflicts':[],
      'font_loader_change':'Signed external catalog: compiled public key and bounded parser only. Preserve native collection, path/hash/size checks and native fallback.',
      'product_stack_sha256':data['stack_sha256'],'compiled':False,'runtime_verified':False,'installed_package_changed':False}
    write_if_changed(ROOT/'build/windows157-port.json',(json.dumps(review,indent=2)+'\n').encode())
    print(json.dumps({'stack':data['stack_sha256'],'inputs':review['target_source_inputs'],'upstreamChanged':len(review['upstream_changed_files'])}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work',type=Path,required=True)
    main(parser.parse_args().work.resolve())
