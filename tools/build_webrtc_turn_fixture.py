"""Build or verify reuse of the pinned loopback-only TURN acceptance helper."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
try:
    from .verify_native_session_runtime import resolve_go_tool
except ImportError:
    from verify_native_session_runtime import resolve_go_tool

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--go',default='go');args=parser.parse_args()
    source=Path(__file__).parent/'webrtc-loopback-turn';output=args.output.absolute();output.parent.mkdir(parents=True,exist_ok=True)
    receipt=output.with_suffix('.build.json');discovery={};go=resolve_go_tool(args.go,discovery)
    inputs={n:sha(source/n) for n in ('main.go','go.mod','go.sum')}
    env=dict(os.environ,GOTOOLCHAIN='local',GOWORK='off',GOFLAGS='')
    version=subprocess.check_output([str(go),'version'],env=env,text=True).strip()
    identity={'inputs':inputs,'goVersion':version,'goSHA256':sha(go)}
    if output.exists():
        prior=json.loads(receipt.read_text()) if receipt.exists() else {}
        if prior.get('identity')!=identity or prior.get('executableSHA256')!=sha(output):
            raise ValueError('Existing TURN helper is not a verified matching build')
        print(json.dumps({'reused':True,**prior}));return
    with output.with_suffix('.build.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(go),'build','-trimpath','-buildvcs=false','-o',str(output),'.'],cwd=source,env=env,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    if {n:sha(source/n) for n in inputs}!=inputs:
        raise ValueError('Building the fixture changed its locked inputs')
    result={'identity':identity,'executableSHA256':sha(output),'discovery':discovery}
    receipt.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
