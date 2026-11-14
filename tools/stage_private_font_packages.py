"""Stage pinned SIL fonts into an application directory, never Windows Fonts."""
import argparse,hashlib,io,json,tarfile,urllib.request
from pathlib import Path
def digest(data):return hashlib.sha256(data).hexdigest()
def write_if(file,data):
 if not file.exists() or file.read_bytes()!=data:file.write_bytes(data)
def stage(manifest,output,cache):
 manifest,output,cache=map(Path,(manifest,output,cache));output=output.resolve();cache=cache.resolve()
 if str(output).lower().startswith(str(Path(r'C:\Windows\Fonts')).lower()):raise ValueError('Do not install application font resources into Windows')
 output.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True);data=json.loads(manifest.read_text('utf-8'))
 for row in [*data['fonts'],*data['licenses']]:
  name=row['file']
  if Path(name).name!=name or '/' in name or '\\' in name or name in ('.','..'):raise ValueError('Unsafe asset name')
  dest=output/name
  if dest.exists() and dest.stat().st_size==row['size'] and digest(dest.read_bytes())==row['sha256']:continue
  if dest.exists():raise ValueError('Existing private asset differs; use a fresh owned directory')
  archive='archive_url' in row;url=row['archive_url'] if archive else row['url'];expected=row['archive_sha256'] if archive else row['sha256']
  if not url.startswith('https://'):raise ValueError('Pinned HTTPS source required')
  cached=cache/expected
  if cached.exists():payload=cached.read_bytes()
  else:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ShardX-private-font-resources'}),timeout=90) as response:payload=response.read(64*1024*1024+1)
   if len(payload)>64*1024*1024 or digest(payload)!=expected:raise ValueError('Font download identity mismatch')
   write_if(cached,payload)
  if digest(payload)!=expected:raise ValueError('Cached font identity mismatch')
  if archive:
   with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as tar:
    member=tar.getmember(row['member'])
    if not member.isfile() or member.size!=row['size']:raise ValueError('Unexpected font archive member')
    payload=tar.extractfile(member).read()
  if len(payload)!=row['size'] or digest(payload)!=row['sha256']:raise ValueError('Font asset identity mismatch')
  write_if(dest,payload)
 target=output/'private-font-packages.json'
 if target.exists() and target.read_bytes()!=manifest.read_bytes():raise ValueError('Existing resource manifest differs; use a fresh owned directory')
 write_if(target,manifest.read_bytes())
 return {'font_files':len(data['fonts']),'license_files':len(data['licenses']),'manifest_sha256':digest(manifest.read_bytes()),'install_to_windows':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);p.add_argument('--cache',required=True);a=p.parse_args();print(json.dumps(stage(a.manifest,a.output,a.cache)))
