"""Read-only installation facts. This does not assign a country or verify a profile."""
import argparse,ctypes,datetime,hashlib,json,os,platform,struct,subprocess,winreg
from pathlib import Path
def registry(hive,key):
 try:
  with winreg.OpenKey(hive,key) as k:return {winreg.EnumValue(k,i)[0]:winreg.EnumValue(k,i)[1] for i in range(winreg.QueryInfoKey(k)[1])}
 except OSError as e:return {'capture_error':str(e)}
def children(hive,key):
 try:
  with winreg.OpenKey(hive,key) as k:return [winreg.EnumKey(k,i) for i in range(winreg.QueryInfoKey(k)[0])]
 except OSError:return []
def font_metadata(file):
 b=file.read_bytes();u16=lambda n:struct.unpack_from('>H',b,n)[0];u32=lambda n:struct.unpack_from('>I',b,n)[0]
 offsets=[u32(12+4*i) for i in range(u32(8))] if b[:4]==b'ttcf' else [0];faces=[]
 for index,face in enumerate(offsets):
  tables={b[face+12+16*i:face+16+16*i]:u32(face+20+16*i) for i in range(u16(face+4))}
  names={}
  if b'name' in tables:
   base=tables[b'name'];storage=base+u16(base+4)
   for i in range(u16(base+2)):
    p,e,lang,n,length,off=struct.unpack_from('>6H',b,base+6+12*i)
    if p not in (0,3) or n not in (1,2,4,5,6,16,17,21,25):continue
    try:value=b[storage+off:storage+off+length].decode('utf-16-be')
    except UnicodeError:continue
    names.setdefault(str(n),[]).append({'languageId':lang,'value':value})
  weight=stretch=None
  if b'OS/2' in tables:weight,stretch=struct.unpack_from('>2H',b,tables[b'OS/2']+4)
  faces.append({'faceIndex':index,'names':names,'weight':weight,'stretch':stretch,'variable':b'fvar' in tables})
 return {'sha256':hashlib.sha256(b).hexdigest(),'size':len(b),'faces':faces}
def main():
 if os.name!='nt':raise RuntimeError('Collect Windows installation facts on Windows')
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
 version=registry(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Windows NT\CurrentVersion')
 langs=registry(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\Nls\Language')
 user=registry(winreg.HKEY_CURRENT_USER,r'Control Panel\International\User Profile')
 office=registry(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Office\ClickToRun\Configuration')
 office={k:office.get(k) for k in ('ProductReleaseIds','VersionToReport','ClientCulture','InstallationPath')}
 api=ctypes.WinDLL('kernel32.dll');api.GetSystemDefaultUILanguage.restype=ctypes.c_ushort;api.GetUserDefaultUILanguage.restype=ctypes.c_ushort
 sfc=ctypes.WinDLL('sfc.dll').SfcIsFileProtected;sfc.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p];sfc.restype=ctypes.c_int
 registrations=[];files=set()
 for hive,label in ((winreg.HKEY_LOCAL_MACHINE,'HKLM'),(winreg.HKEY_CURRENT_USER,'HKCU')):
  values=registry(hive,r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
  for name,value in values.items():
   if not isinstance(value,str):continue
   file=Path(os.path.expandvars(value));file=file if file.is_absolute() else Path(os.environ['WINDIR'])/'Fonts'/file
   registrations.append({'scope':label,'registrationName':name,'path':str(file),'exists':file.is_file()})
   if file.is_file() and file.suffix.lower() in ('.ttf','.ttc','.otf'):files.add(file.resolve())
 for file in (Path(os.environ['WINDIR'])/'Fonts').iterdir():
  if file.is_file() and file.suffix.lower() in ('.ttf','.ttc','.otf'):files.add(file.resolve())
 office_files={};office_dir=Path(office.get('InstallationPath') or r'C:\Program Files\Microsoft Office')/'root/VFS/Fonts'
 if office_dir.is_dir():
  for file in office_dir.rglob('*'):
   if file.is_file() and file.suffix.lower() in ('.ttf','.ttc','.otf'):office_files.setdefault(hashlib.sha256(file.read_bytes()).hexdigest(),[]).append(str(file))
 rows=[];errors=[]
 for file in sorted(files):
  try:
   info=font_metadata(file);protected=bool(sfc(None,str(file)));matches=office_files.get(info['sha256'],[])
   rows.append({'path':str(file),**info,'windowsProtected':protected,'origin':{'kind':'windows-protected-component-unresolved' if protected else 'application-visible' if matches else 'unattributed','evidence':'SfcIsFileProtected' if protected else 'exact installed Office VFS file hash' if matches else 'actual font registration and bytes only','applicationVersion':office.get('VersionToReport') if matches else None,'matchingInstalledOfficeFiles':matches}})
  except Exception as e:errors.append({'path':str(file),'error':str(e)})
 package_key=r'SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages';packages=[]
 for name in children(winreg.HKEY_LOCAL_MACHINE,package_key):
  if any(word in name.lower() for word in ('languagepack','languagefeatures','font')):
   v=registry(winreg.HKEY_LOCAL_MACHINE,package_key+'\\'+name);packages.append({'identity':name,'currentStateRaw':v.get('CurrentState'),'source':'CBS registry; raw state retained without inferring a completed component inventory'})
 try:
  r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',"[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); [ordered]@{OS=Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture;Hotfixes=@(Get-HotFix | Select-Object HotFixID,InstalledOn)} | ConvertTo-Json -Depth 4"],capture_output=True,timeout=40);updates={'exitCode':r.returncode,'rawJSON':r.stdout.decode('utf-8','strict'),'error':r.stderr.decode('utf-8','replace')}
 except Exception as e:updates={'error':str(e)}
 value={'schema_version':3,'kind':'live-host-installation-facts','countryEntry':None,'defaultEligible':False,'windows':{k:version.get(k) for k in ('ProductName','EditionID','DisplayVersion','CurrentBuild','CurrentBuildNumber','UBR','BuildLabEx','InstallationType')},'architecture':platform.machine(),'language':{'nls':langs,'systemUILanguageId':api.GetSystemDefaultUILanguage(),'userUILanguageId':api.GetUserDefaultUILanguage(),'userLanguageTags':user.get('Languages'),'installedUILanguageKeys':children(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\MUI\UILanguages')},'officeClickToRun':office,'registeredFonts':registrations,'fontFiles':rows,'languageAndFontPackagesRaw':packages,'updates':updates,'errors':errors,'gaps':['Browser-visible full inventory must be captured separately.','Raw CBS package states do not certify a complete DISM capability inventory.','No country mapping or application-absent alternate is inferred.','Windows-protected files still require language/base component ownership attribution.']}
 value['capturedAtUTC']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 content=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode();(out/'installation-facts.json').write_bytes(content);(out/'SHA256.json').write_text(json.dumps({'installation-facts.json':hashlib.sha256(content).hexdigest()},indent=2)+'\n')
 print(json.dumps({'output':str(out),'build':version.get('CurrentBuildNumber'),'ubr':version.get('UBR'),'observedFontFiles':len(rows),'protectedFiles':sum(r['windowsProtected'] for r in rows),'officeFileMatches':sum(bool(r['origin']['matchingInstalledOfficeFiles']) for r in rows),'countryAssigned':False,'defaultEligible':False}))
if __name__=='__main__':main()
