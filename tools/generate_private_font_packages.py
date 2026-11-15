"""Render the exact native font asset table; fonts remain external hash pins."""
import base64,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def render():
 manifest=ROOT/'build/private-font-packages.json';data=json.loads(manifest.read_text('utf-8'));rows=[]
 for font,items in [(True,data['fonts']),(False,data['licenses'])]:
  for item in items:
   if not re.fullmatch(r'[A-Za-z0-9_.-]+',item['file']) or not re.fullmatch(r'[a-f0-9]{64}',item['sha256']):raise ValueError('Unsafe font manifest entry')
   rows.append((item['file'],item['sha256'],item['size'],font))
 rows.append(('private-font-packages.json',hashlib.sha256(manifest.read_bytes()).hexdigest(),manifest.stat().st_size,False))
 entries='inline constexpr Asset kAssets[] = {\n'+''.join('  {L'+json.dumps(name)+','+json.dumps(sha)+','+str(size)+'ULL,'+str(font).lower()+'},\n' for name,sha,size,font in rows)+'};'
 key=json.loads((ROOT/'build/font-catalog-key.json').read_text('utf-8'))
 public=base64.b64decode(key['cngPublicBlob'],validate=True)
 if key['schemaVersion']!=2 or key['algorithm']!='ECDSA-P256-SHA256-P1363' or len(public)!=72 or hashlib.sha256(public).hexdigest()!=key['keyId']:raise ValueError('Invalid catalog trust anchor')
 anchor='inline constexpr unsigned char kCatalogPublicKey[] = {'+','.join(str(b) for b in public)+'};'
 loader=(ROOT/'tools/signed_font_catalog_win.h.in').read_text('utf-8').replace('// TRUSTED_CATALOG_PUBLIC_KEY',anchor)
 return (ROOT/'tools/private_font_packages_win.h.in').read_text('utf-8').replace('// PINNED_ASSETS',entries).replace('// SIGNED_CATALOG_LOADER',loader)
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');p.add_argument('--check',action='store_true');a=p.parse_args()
 if a.write or a.check:
  from generate_windows_font_packages import source_from_patch,replace_created
  file=ROOT/'patches/lite-windows157/13-fonts.patch';patch=file.read_text('utf-8');name='base/uxr_private_font_packages_win.h';expected=render()
  if a.check:
   if source_from_patch(patch,name)!=expected:raise SystemExit('Private font source differs from frozen asset pins')
  else:file.write_text(replace_created(patch,name,expected),encoding='utf-8',newline='\n')
 else:print(render(),end='')
