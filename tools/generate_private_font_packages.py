"""Render the exact native font asset table; fonts remain external hash pins."""
import hashlib,json,re
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
 country=json.loads((ROOT/'build/country-font-resources.json').read_text('utf-8'))
 groups=[];choices=[]
 for name,assets in sorted(country['groups'].items()):
  if not re.fullmatch(r'[a-z-]+',name) or not assets:raise ValueError('Invalid country resource group')
  symbol='kCountry_'+name.replace('-','_');lines=[]
  for asset in assets:
   if not re.fullmatch(r'[a-f0-9]{64}',asset['sha256']) or asset['file']!=asset['sha256']+'.blob' or asset['kind'] not in ('font','license') or not 0<asset['size']<128*1024*1024:raise ValueError('Invalid country resource pin')
   lines.append('  {L'+json.dumps(asset['file'])+','+json.dumps(asset['sha256'])+','+str(asset['size'])+'ULL,'+str(asset['kind']=='font').lower()+'},\n')
  groups.append('inline constexpr Asset '+symbol+'[] = {\n'+''.join(lines)+'};\n')
  choices.append('  if (group == '+json.dumps(name)+') { *count = sizeof('+symbol+')/sizeof('+symbol+'[0]); return '+symbol+'; }\n')
 countries=''.join(groups)+'inline const Asset *CountryAssets(std::string_view group, size_t *count) {\n  if (!count) return nullptr;\n  *count = 0;\n'+''.join(choices)+'  return nullptr;\n}\n'
 return (ROOT/'tools/private_font_packages_win.h.in').read_text('utf-8').replace('// PINNED_ASSETS',entries).replace('// PINNED_COUNTRY_ASSETS',countries)
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
