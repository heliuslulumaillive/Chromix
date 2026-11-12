"""Run/verify document-loopback acceptance without mutating an immutable bundle."""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
try:
    from . import verify_native_session_runtime as session
    from . import webrtc_document_loopback_acceptance as acceptance
except ImportError:
    import verify_native_session_runtime as session
    import webrtc_document_loopback_acceptance as acceptance

REPO=Path(__file__).resolve().parents[1]

def validate_existing(report_path, bundle, source_record):
    report_path=Path(report_path);bundle=Path(bundle)
    data=json.loads(report_path.read_text(encoding='utf-8-sig'))
    source,binding=session.input_identity(bundle/'chrome.exe',Path(source_record))
    if data.get('source')!=source or data.get('binding')!=binding:
        raise ValueError('WebRTC acceptance does not match the exact source/build identity')
    if not session.execution_passed(data.get('execution',{})):
        raise ValueError('WebRTC acceptance did not complete on the owned private desktop')
    session.validate_runtime_isolation(report_path,bundle,data.get('runtime_isolation'))
    result=acceptance.validate(data,exe_sha256=binding['browser_sha256'],
        dll_sha256=binding['chrome_dll_sha256'],harness=acceptance.source_hashes(REPO))
    return {**result,'report_sha256':acceptance.digest(report_path)}

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--source-record',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--turn-helper',type=Path)
    parser.add_argument('--node',default='node');parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args(argv)
    if args.verify_only:
        print(json.dumps(validate_existing(args.report,args.browser.parent,args.source_record)));return
    if not args.turn_helper:parser.error('--turn-helper is required for execution')
    destination=session.security.plain(args.report.absolute())
    if destination.exists():raise FileExistsError('Use a new WebRTC report path')
    destination.parent.mkdir(parents=True,exist_ok=True)
    source,binding=session.input_identity(args.browser,args.source_record)
    original=session.bundle_inventory(args.browser.parent)
    temporary=Path(tempfile.mkdtemp(prefix='webrtc-document-runtime-',dir=destination.parent))
    runtime=temporary/'runtime'
    isolation=session.prepare_runtime_copy(args.browser.parent,runtime,original)
    node=shutil.which(args.node)
    if not node:raise ValueError('Node runtime is unavailable')
    node=Path(node).resolve(strict=True)
    command=[str(node),str(REPO/'tools/webrtc_document_loopback_runtime.mjs'),
             '--browser='+str(runtime/args.browser.name),'--report='+str(destination),
             '--turn-helper='+str(args.turn_helper.absolute()),'--proxy=controlled','--headed']
    execution=session.run_private_desktop(command,environment=session.safe_environment(temporary),
        cwd=REPO,log=destination.with_suffix('.execution.log'),timeout=540)
    data=json.loads(destination.read_text()) if destination.exists() else {'status':'failed','error':'No native runtime result'}
    data.update(source=source,binding=binding,execution=execution)
    try:
        data['runtime_isolation']=session.finish_runtime_copy(args.browser.parent,runtime,original,destination,isolation)
        destination.write_text(json.dumps(data,indent=2),encoding='utf-8')
        result=validate_existing(destination,args.browser.parent,args.source_record)
    except Exception as error:
        data.update(status='failed',wrapperError=str(error))
        destination.write_text(json.dumps(data,indent=2),encoding='utf-8')
        raise
    print(json.dumps(result))

if __name__=='__main__':main()
