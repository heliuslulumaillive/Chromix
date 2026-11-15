"""Strict raw and domain-substituted selected-source replay; no kernel build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))
from tools import lite_patch_stack as stack,apply_restored_patches as arp
from tools.prepare_google_services import apply_patch,pinned_inverse_rules
from tools.prepare_lite_product import prepare
from tools.patch_selection import preparation_key
from tools.google_services_inputs import write_if_changed


def replay(port,output,patch_bin,resume=False):
    if output.exists() and not resume:raise ValueError('Use a fresh owned replay output or explicitly resume its verified prepared tree')
    product=stack.load(ROOT);source=port/'new/baseline';core=port/'new/tooling/core';windows=port/'new/tooling/windows'
    selected={row['path'] for row in product['source_files']}
    listed=[name for name in (windows/'domain_substitution.list').read_text().splitlines() if name in selected]
    rules=arp._rules((core/'domain_regex.list').read_bytes())
    reports={}
    for label in ['raw','substituted']:
        work=output/label;src=work/'src'
        if src.exists():
            if not resume or not (work/'google-services-source.json').is_file():raise ValueError('Cannot resume an incomplete source preparation')
            result=prepare(ROOT,work)
        else:
            result=None;shutil.copytree(source,src)
        version=product['runtime']['chromium_version'].split('.')
        write_if_changed(src/'chrome/VERSION',''.join(f'{k}={v}\n' for k,v in zip(['MAJOR','MINOR','BUILD','PATCH'],version)).encode())
        for original,destination in [(core/'domain_regex.list',work/'tooling/ungoogled-chromium/domain_regex.list'),(windows/'domain_substitution.list',work/'tooling/ungoogled-chromium-windows/domain_substitution.list')]:
            write_if_changed(destination,('\n'.join(listed)+'\n').encode() if original.name=='domain_substitution.list' else original.read_bytes())
        if result is None and label=='substituted':
            for name in listed:
                file=src/name
                if not file.is_file():continue
                text,encoding=arp._decode(file.read_bytes());write_if_changed(file,arp._substitute(text,rules).encode(encoding))
            arp.run_apply(src,ROOT,core,windows,'windows',patch_bin,profile='lite')
        elif result is None:
            for entry in product['patches']:apply_patch(src,patch_bin,ROOT/entry['path'])
            for name in listed:
                file=src/name
                if not file.is_file():continue
                text,encoding=arp._decode(file.read_bytes());write_if_changed(file,arp._substitute(text,rules).encode(encoding))
        write_if_changed(src/'.chromix-patches',(preparation_key(ROOT,'windows','windows',profile='lite')+'\n').encode())
        result=prepare(ROOT,work)
        stack.verify_source(ROOT,src,inverse_rules=pinned_inverse_rules(core/'domain_regex.list'))
        before={p.relative_to(src).as_posix():(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in src.rglob('*') if p.is_file()}
        marker=(work/'google-services-source.json').stat().st_mtime_ns
        prepare(ROOT,work)
        assert marker==(work/'google-services-source.json').stat().st_mtime_ns
        assert before=={p.relative_to(src).as_posix():(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in src.rglob('*') if p.is_file()}
        reports[label]={'status':'passed','sourceFiles':len(product['source_files']),'sourceChecks':len(result['source_checks']),'idempotent':True}
    for row in product['source_files']:
        a=output/'raw/src'/row['path'];b=output/'substituted/src'/row['path']
        assert a.is_file()==b.is_file()
        if a.is_file():assert a.read_bytes()==b.read_bytes(),row['path']
    report={'status':'passed','version':product['runtime']['chromium_version'],'stackSHA256':product['stack_sha256'],'cases':reports,'compiled':False,'domainScope':'Selected product inputs; hosted preparation retains the full pinned upstream file list','runtimeAcceptance':'pending'}
    write_if_changed(output/'report.json',(json.dumps(report,indent=2)+'\n').encode());print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--patch-bin',required=True);p.add_argument('--resume',action='store_true')
    a=p.parse_args();replay(a.port.resolve(),a.output.resolve(),a.patch_bin,a.resume)
