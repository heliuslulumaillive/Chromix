"""Admit the intact v3 stage-1 checkpoint for a cache-only recovery."""
import json
from pathlib import Path
import subprocess
from tools.security_build_record import recipe
from tools.lite_patch_stack import load

ROOT=Path(__file__).resolve().parents[1]
DONOR='6d2736013ed645484995df0117a3ffafda942b87'
SOURCE_RECIPE='deb1c6013dc55fdbf60c78000fb2e2afe337c8c59e32483a82c167b1b94116ea'

def write(path,value):
    content=(json.dumps(value,indent=2)+'\n').encode()
    if not path.exists() or path.read_bytes()!=content:path.write_bytes(content)

def main():
    previous=json.loads(subprocess.check_output(['git','show',DONOR+':build/lite-windows157.json'],cwd=ROOT))
    current=load(ROOT)
    assert previous['stack_sha256']==current['stack_sha256']
    assert previous['source_files']==current['source_files']
    identity=recipe(ROOT)
    rule=dict(id='lite15778-metagen-cache-placement-recovery-v1',chromium_version='157.0.8103.78',
        core_commit='8c886dffc6ade39d8f7df5d64ace5a8824b455e1',windows_commit='20e9a348fc616bd73a366fbd5d8881613d6c5cb0',arch='x64',
        source_commit=DONOR,source_run='43953428441',source_stage=1,
        source_recipe_sha256=SOURCE_RECIPE,target_recipe_sha256=identity,
        source_preparation_sha256=SOURCE_RECIPE,target_preparation_sha256=identity,
        source_aggregate_sha256=current['stack_sha256'],target_aggregate_sha256=current['stack_sha256'],
        source_patch_kind='lite-windows157-product',target_patch_kind='lite-windows157-product',
        scope='Exact intact v3 stage-1 donor, never the stage-2 invalidated tree. No compiled source replacement. Relocate only the two hash-pinned Python cache files after authenticating the complete cache-bearing compiler fingerprints and original marker, then require the unchanged full compiler fingerprints to be restored. Keep every compiled object and original timestamp; reject all other tool drift. Retain future bindings outside toolchain trees and bootstrap GN only after restoration finishes. No validation exemption or DLL admission.')
    path=ROOT/'build/checkpoint-migrations.json';data=json.loads(path.read_bytes())
    data['migrations']=[r for r in data['migrations'] if r['id']!=rule['id']]+[rule];write(path,data)
    assert recipe(ROOT)==identity
    result=dict(recipeSHA256=identity,stackSHA256=current['stack_sha256'],donorRun='43953428441',donorStage=1,sourceUpdates=[],ruleId=rule['id'])
    write(ROOT/'build/metagen-cache-recovery-plan.json',result);print(json.dumps(result))

if __name__=='__main__':main()
