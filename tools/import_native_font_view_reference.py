"""Import reviewed file identities; never copies or installs Windows fonts."""
import argparse,hashlib,importlib.util,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--objects',required=True);a=p.parse_args();evidence=Path(a.evidence);objects=Path(a.objects)
load=lambda p:json.loads(p.read_text('utf-8-sig'))
proof=load(evidence/'final-verification.json');assert proof['pairedPostScriptFamilyStyleAndBlobSourcesExactMatch'] and proof['allDifferentFullNamesMappedToSameSourceSFNT']
base=load(evidence/'final-base-profile.json');hans=load(evidence/'hans-language-module-reference.json');inbox=load(evidence/'inbox-terminal-font-source-attribution.json');host=load(evidence/'host-terminal-shared-fonts.json')
pin=lambda row:{'file':row.get('fileName') or Path(row.get('path','')).name,'sha256':row['sha256'],'size':row.get('bytes',row.get('size'))}
data={'schema_version':1,'kind':'source-verified-native-font-view-v1','referenceProfile':base['profileId'],'referenceEvidenceSHA256':hashlib.sha256((evidence/'final-verification.json').read_bytes()).hexdigest(),'core':[pin(f) for f in base['files']],'hans':[pin(f) for f in hans['files']],'inboxPackages':[{'package':inbox['package']['PackageFullName'],'fonts':[pin(f) for f in inbox['files']]},{'package':host['package']['PackageFullName'],'fonts':[pin(f) for f in host['files']]}],'hansCountry':'CN','hansGroup':'hans-cn','unknownSourcePolicy':'excluded-from-controlled-view','unsupportedRequiredSourcePolicy':'reject-controlled-startup','nativeMode':'unchanged complete original collection','redistributeWindowsFontFiles':False}
assert len(data['core'])==143 and len(data['hans'])==8 and all(len(p['fonts'])==4 for p in data['inboxPackages'])
(ROOT/'build/native-font-view.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
manifest=ROOT/'build/country-font-resources.json';catalog=json.loads(subprocess.check_output(['git','show','96173fd8dd4c883cd3201f2795860474b7effd52:build/country-font-resources.json'],cwd=ROOT));previous=catalog['countries']['CN'];assert [c for c,g in catalog['countries'].items() if g==previous]==['CN']
spec=importlib.util.spec_from_file_location('native_font_metadata',ROOT/'tools/capture_windows_font_environment.py');reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
removed=set()
for group,assets in catalog['groups'].items():
    for asset in assets:
        if asset['kind']!='font' or asset['sha256'] in removed:continue
        file=objects/asset['file'];metadata=reader.font_metadata(file)
        assert metadata['sha256']==asset['sha256'] and metadata['size']==asset['size']
        families={n['value'] for face in metadata['faces'] for name_id in ('1','16') for n in face['names'].get(name_id,[]) if n['languageId']==1033}
        if families&{'Noto Sans SC','Noto Serif SC'}:removed.add(asset['sha256'])
assert len(removed)==2
for group in catalog['groups']:
    catalog['groups'][group]=[asset for asset in catalog['groups'][group] if asset['sha256'] not in removed]
    catalog['groupFamilies'][group]=[name for name in catalog['groupFamilies'][group] if name not in ('Noto Sans SC','Noto Serif SC')]
catalog['groups']['hans-cn']=catalog['groups'].pop(previous);catalog['groupFamilies']['hans-cn']=catalog['groupFamilies'].pop(previous);catalog['countries']['CN']='hans-cn'
catalog['kind']='hash-pinned-controlled-country-font-resources';catalog['fallback']='verified-common-native-view-without-regional-additions'
catalog['nativeView']={'version':1,'coreFiles':143,'hansFiles':8,'hansCountry':'CN','inboxSharedFonts':'registered supported Terminal package versions','excludeUnselectedNativeFonts':True}
manifest.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({'core':143,'hans':8,'inboxVariants':len(data['inboxPackages']),'removedDuplicateOpenSCAssets':sorted(removed),'countries':len(catalog['countries']),'groups':len(catalog['groups'])}))
