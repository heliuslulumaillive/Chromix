"""Read-only prediction of staged Windows text bytes for the exact recipe."""
import hashlib,json,subprocess
from pathlib import Path
from lite_patch_stack import input_hashes
ROOT=Path(__file__).resolve().parents[1]
def git(args,**kwargs):return subprocess.check_output(['git',*args],cwd=ROOT,**kwargs)
expected=input_hashes(ROOT);attributes={name:{} for name in expected}
raw=git(['check-attr','text','eol','--stdin'],input=('\n'.join(expected)+'\n').encode()).decode()
for line in raw.splitlines():
    name,attribute,value=line.rsplit(': ',2);attributes[name][attribute]=value
autocrlf=git(['config','--get','core.autocrlf'],text=True).strip();different=[]
for name,digest in expected.items():
    data=git(['show',':'+name]);attrs=attributes[name]
    crlf=attrs['eol']=='crlf' or (attrs['eol']=='unspecified' and attrs['text']=='auto' and autocrlf=='true' and b'\0' not in data)
    if crlf:data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
    if hashlib.sha256(data).hexdigest()!=digest:different.append(name)
result={'recipeInputs':len(expected),'stagedWindowsCheckoutMatchesLocal':not different,'differentInputs':different,'coreAutocrlf':autocrlf,'nativeTemplateEOL':attributes['tools/native_font_view_win.h.in']['eol']}
print(json.dumps(result));assert not different,'Staged Windows recipe inputs differ from the reviewed local recipe'
