"""Freeze an exact .78 two-source migration while retaining compiled objects."""
import hashlib
import json
from pathlib import Path
import subprocess
from tools.security_build_record import recipe
from tools.lite_patch_stack import load
from tools.lite_product_migration import reviewed_updates

ROOT=Path(__file__).resolve().parents[1]
DONOR='d9727f1a769a855da9d970980b80b230aec2bf1c'
DONOR_RECIPE='82e5d93687ae10dc7b03826ad534db7e57c3a5d27459056ea00fc900494d6326'

def write(file,value):
    content=(json.dumps(value,indent=2)+'\n').encode()
    if not file.exists() or file.read_bytes()!=content:file.write_bytes(content)

def main():
    donor=json.loads(subprocess.check_output(['git','show',DONOR+':build/lite-windows157.json'],cwd=ROOT))
    target=load(ROOT)
    before={r['path']:r['after_sha256'] for r in donor['source_files']}
    after={r['path']:r['after_sha256'] for r in target['source_files']}
    assert before.keys()==after.keys()
    changed={name for name in before if before[name]!=after[name]}
    assert changed=={'base/uxr_private_font_packages_win.h','ui/gfx/win/direct_write.cc'}
    updates=[]
    for name in sorted(changed):
        replacement='build/checkpoint-sources/'+after[name]+Path(name).suffix
        assert hashlib.sha256((ROOT/replacement).read_bytes()).hexdigest()==after[name]
        updates.append(dict(path=name,before_sha256=before[name],after_sha256=after[name],replacement_path=replacement))
    identity=recipe(ROOT)
    rule=dict(id='lite15778-signed-source-controlled-fonts-v3',chromium_version='157.0.8103.78',
        core_commit='8c886dffc6ade39d8f7df5d64ace5a8824b455e1',windows_commit='20e9a348fc616bd73a366fbd5d8881613d6c5cb0',arch='x64',
        source_commit=DONOR,source_run='43775379956',source_stage=4,
        source_recipe_sha256=DONOR_RECIPE,target_recipe_sha256=identity,
        source_preparation_sha256=DONOR_RECIPE,target_preparation_sha256=identity,
        source_aggregate_sha256=donor['stack_sha256'],target_aggregate_sha256=target['stack_sha256'],
        source_patch_kind='lite-windows157-product',target_patch_kind='lite-windows157-product',product_source_updates=updates,
        scope='Exact .78 checkpoint migration changing only the private font helper and common DirectWrite entry. Signed data chooses common/shared/regional source pins and managed additions; no country/source table is compiled. Optional additions retain only the authenticated controlled view; invalid required policy/sources reject startup. Preserve named faces, axes, source/path/hash checks and read-only lifetimes. Verified LLVM bindings persist in the toolchain checkpoint with bounded HTTP retries. No .38 objects, relaxed source checks, DLL enrollment or profile migration.')
    file=ROOT/'build/checkpoint-migrations.json';data=json.loads(file.read_bytes())
    data['migrations']=[r for r in data['migrations'] if r['id']!=rule['id']]+[rule]
    write(file,data);reviewed_updates(ROOT,rule)
    assert recipe(ROOT)==identity
    result=dict(recipeSHA256=identity,stackSHA256=target['stack_sha256'],ruleId=rule['id'],donorRun=rule['source_run'],donorStage=4,sourceUpdates=updates)
    write(ROOT/'build/external-source-view-upgrade-plan.json',result);print(json.dumps(result))

if __name__=='__main__':main()
