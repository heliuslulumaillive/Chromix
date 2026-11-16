"""Pin the exact two-source .38 transition; no cache identity relaxation."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def write(file,value):
    data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8')
    if not file.exists() or file.read_bytes()!=data:file.write_bytes(data)
paths=subprocess.check_output(['git','diff','--name-only'],cwd=ROOT,text=True).splitlines()+subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
for relative in paths:
    file=ROOT/relative
    if file.is_file() and file.suffix in ('.py','.cc','.h','.in','.json','.patch','.md'):
        data=file.read_bytes()
        if b'\r\n' in data:file.write_bytes(data.replace(b'\r\n',b'\n'))
file=ROOT/'build/lite-windows157.json';manifest=json.loads(file.read_text('utf8'))
for patch in manifest['patches']:patch['sha256']=sha(ROOT/patch['path'])
manifest['runtime']['country_font_resources']['manifest_sha256']=sha(ROOT/'build/country-font-resources.json')
manifest['runtime']['native_font_view']['manifest_sha256']=sha(ROOT/'build/native-font-view.json')
payload={k:v for k,v in manifest.items() if k!='stack_sha256'};manifest['stack_sha256']=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest();write(file,manifest)
from security_build_record import recipe
from lite_patch_stack import load
load(ROOT);target=recipe(ROOT)
updates=json.loads((ROOT/'build/native-font-view-source-updates.json').read_text('utf8'))['updates']
source_commit='96173fd8dd4c883cd3201f2795860474b7effd52'
rule={'id':'lite157-source-controlled-country-fonts-v1','chromium_version':'157.0.8103.38','core_commit':'de09e0c70c6bc1ac83d4ecdfd0e82c7e250c9a3c','windows_commit':'f85bc55429c37b1c748b676b18fe79b38eee3207','arch':'x64','source_commit':source_commit,'source_run':'43767199807','source_stage':1,'source_recipe_sha256':'aaee48253c5d23b08e3dee191309ee68252c58e0fd50aee20426bdb17d0b9974','target_recipe_sha256':target,'source_preparation_sha256':'aaee48253c5d23b08e3dee191309ee68252c58e0fd50aee20426bdb17d0b9974','target_preparation_sha256':target,'source_aggregate_sha256':'e57d291676965e38d3ecb6e3dd85f2fcd735d3426f37726ad8eb00d58aa7d82e','target_aggregate_sha256':manifest['stack_sha256'],'source_patch_kind':'lite-windows157-product','target_patch_kind':'lite-windows157-product','product_source_updates':updates,'scope':'Exact two-source same-version change from accepted .38 donor. Filter the original native collection by reviewed source file identities, retaining 143 core files and registered supported inbox shared font variants; activate the eight Fonts.Hans sources only for the CN-specific group. Preserve native face/axis metadata, read-only lifetimes and original native mode. Optional addition failures retain the verified selected collection; required-source failures reject controlled startup. No .78 object reuse, source check weakening, DLL enrollment or formal profile change.'}
migration=ROOT/'build/checkpoint-migrations.json';data=json.loads(migration.read_text('utf8'));key=next(k for k,v in data.items() if isinstance(v,list))
data[key]=[r for r in data[key] if r.get('id')!=rule['id']]+[rule];write(migration,data)
from lite_product_migration import reviewed_updates
reviewed_updates(ROOT,rule)
result={'recipeSHA256':target,'stackSHA256':manifest['stack_sha256'],'ruleId':rule['id'],'donorRun':rule['source_run'],'sourceUpdates':updates}
write(ROOT/'build/native-font-view-upgrade-plan.json',result);print(json.dumps(result))
