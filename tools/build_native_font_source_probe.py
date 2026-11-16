import argparse,json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
vswhere=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
installation=subprocess.check_output([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
if not installation:raise RuntimeError('Native Visual C++ toolchain required')
script=out/'compiler-environment.cmd';script.write_text('@echo off\ncall "'+str(Path(installation)/'VC/Auxiliary/Build/vcvars64.bat')+'" >nul\nif errorlevel 1 exit /b 1\nset\n')
env=dict(os.environ)
for line in subprocess.check_output(['cmd.exe','/d','/c',str(script)],text=True).splitlines():
    if '=' in line:
        key,value=line.split('=',1);env[key]=value
compiler=shutil.which('clang++') or r'C:\Program Files\LLVM\bin\clang++.exe';binary=out/'native-font-source-probe.exe'
subprocess.run([compiler,'-std=c++20','-O2',str(ROOT/'tools/native_font_source_probe.cc'),'-o',str(binary),'-ldwrite'],env=env,check=True)
r=subprocess.run([str(binary)],capture_output=True,check=True);data=json.loads(r.stdout.decode('utf8'))
(out/'native-font-source-probe.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({'nativeFaces':data['nativeFaces'],'nativeFiles':len(data['files']),'terminalSources':[row for row in data['files'] if 'WindowsTerminal_' in row['path']]}))
