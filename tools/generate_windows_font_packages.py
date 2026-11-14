"""Deterministically render the frozen, name-only Windows font package facts."""
import argparse,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'tools/tests/fixtures/windows-font-packages-v2.json'
MARKER='// Windows font packages v2: generated installation facts, never per-face noise.'
def render(data):
 def array(name,items):return 'inline constexpr std::string_view '+name+'[] = {\n'+''.join('  '+json.dumps(s,ensure_ascii=False)+',\n' for s in items)+'};\n'
 lines=[MARKER,'// All published Windows fonts, including optional FOD packs, are retained.',
 '// Source URLs and native family/full/PostScript aliases are in the frozen catalog.',
 '// Only known application packages vary. Unknown installed fonts stay native.',
 array('kWindowsSystemFontNamesV2',data['core_names']),array('kWindowsSystemVariablePrefixesV2',data['system_variable_prefixes']),array('kWindowsOfficeFontNamesV2',data['office_names']),
 'inline constexpr uint32_t kWindowsFontPackageProfilesV2[] = { '+', '.join(str(p['mask'])+'u' for p in data['profiles'])+' };',r'''
inline int WindowsFontCompareV2(std::string_view a, std::string_view b) {
  const auto lower = [](unsigned char c) { return c >= 'A' && c <= 'Z' ? c + ('a'-'A') : c; };
  for (size_t i=0, n=std::min(a.size(),b.size()); i<n; ++i) {
    const auto x=lower(static_cast<unsigned char>(a[i])), y=lower(static_cast<unsigned char>(b[i]));
    if (x!=y) return x<y ? -1 : 1;
  }
  return a.size()==b.size() ? 0 : a.size()<b.size() ? -1 : 1;
}
template <size_t N>
inline bool WindowsFontListedV2(std::string_view name, const std::string_view (&values)[N]) {
  size_t first=0, last=N;
  while (first<last) {
    const auto middle=first+(last-first)/2;
    const int compared=WindowsFontCompareV2(name,values[middle]);
    if (compared==0) return true;
    if (compared<0) last=middle; else first=middle+1;
  }
  return false;
}
inline bool WindowsFontFamilyPrefixV2(std::string_view name, std::string_view prefix) {
  return name.size()>=prefix.size() && AsciiEqual(name.substr(0,prefix.size()),prefix) &&
      (name.size()==prefix.size() || name[prefix.size()]==' ' || name[prefix.size()]=='-');
}
inline uint32_t WindowsFontPackageProfileV2(uint32_t seed) {
  return (seed ^ (seed >> 16)) % 12u;
}
inline uint32_t WindowsFontPackageBitV2(std::string_view name) {
  if (WindowsFontListedV2(name,kWindowsSystemFontNamesV2)) return 0;
  for (const auto prefix:kWindowsSystemVariablePrefixesV2)
    if (name.size()>=prefix.size() && AsciiEqual(name.substr(0,prefix.size()),prefix)) return 0;
  for (const auto core:kProtectedFontNames) if (AsciiEqual(name,core)) return 0;
  for (const auto prefix:{"Noto Sans SC","Noto Sans TC","Noto Sans HK","Noto Sans JP","Noto Sans KR",
      "Noto Sans CJK SC","Noto Sans CJK TC","Noto Sans CJK HK","Noto Sans CJK JP","Noto Sans CJK KR",
      "NotoSansSC","NotoSansTC","NotoSansHK","NotoSansJP","NotoSansKR",
      "NotoSansCJKsc","NotoSansCJKtc","NotoSansCJKhk","NotoSansCJKjp","NotoSansCJKkr"})
    if (WindowsFontFamilyPrefixV2(name,prefix)) return 2u;
  for (const auto prefix:{"Noto Serif SC","Noto Serif TC","Noto Serif HK","Noto Serif JP","Noto Serif KR",
      "Noto Serif CJK SC","Noto Serif CJK TC","Noto Serif CJK HK","Noto Serif CJK JP","Noto Serif CJK KR",
      "NotoSerifSC","NotoSerifTC","NotoSerifHK","NotoSerifJP","NotoSerifKR",
      "NotoSerifCJKsc","NotoSerifCJKtc","NotoSerifCJKhk","NotoSerifCJKjp","NotoSerifCJKkr"})
    if (WindowsFontFamilyPrefixV2(name,prefix)) return 4u;
  for (const auto prefix:{"Liberation Sans","Liberation Serif","Liberation Mono",
                         "LiberationSans","LiberationSerif","LiberationMono"})
    if (WindowsFontFamilyPrefixV2(name,prefix)) return 8u;
  if (WindowsFontListedV2(name,kWindowsOfficeFontNamesV2)) return 1u;
  return 0;  // No provenance, no removal: unknown/system-update fonts stay native.
}
inline bool ShardWindowsFontHiddenV2(std::string_view name, uint32_t seed) {
  if (name.empty() || !seed) return false;
  const auto bit=WindowsFontPackageBitV2(name);
  return bit!=0 && (kWindowsFontPackageProfilesV2[WindowsFontPackageProfileV2(seed)] & bit)==0;
}
''']
 return '\n'.join(lines)+'\n'
def replace_created(patch,name,source):
 start=patch.index('diff --git a/'+name+' b/'+name+'\n');end=patch.find('diff --git ',start+1)
 if end<0:end=len(patch)
 old=patch[start:end];hunk=re.search(r'@@ -0,0 \+1,\d+ @@\n',old)
 if not hunk:raise ValueError('Not a complete created source: '+name)
 new=old[:hunk.start()]+'@@ -0,0 +1,'+str(len(source.splitlines()))+' @@\n'+''.join('+'+line for line in source.splitlines(keepends=True))
 return patch[:start]+new+patch[end:]
def source_from_patch(patch,name):
 start=patch.index('diff --git a/'+name+' b/'+name+'\n');end=patch.find('diff --git ',start+1)
 part=patch[start:] if end<0 else patch[start:end]
 return ''.join(line[1:] for line in part.splitlines(keepends=True) if line.startswith('+') and not line.startswith('+++'))
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
 data=json.loads(CATALOG.read_text('utf-8'));file=ROOT/'patches/lite-windows157/19-shared-policy.patch';patch=file.read_text('utf-8');name='base/uxr_surface_reference_data.h';source=source_from_patch(patch,name)
 start=source.find(MARKER);end=source.index('struct TextReferenceRow {')
 if start<0:start=end
 result=source[:start]+render(data)+source[end:]
 if args.check:
  if result!=source:raise SystemExit('Frozen font package header is not regenerated')
 else:file.write_text(replace_created(patch,name,result),encoding='utf-8',newline='\n')
if __name__=='__main__':main()
