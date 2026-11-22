import configparser
import json
import io
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import chromium158_base as native, lite_patch_stack

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = {'chrome/app/etw_events/MSG00001.bin', 'chrome/app/etw_events/chrome_events_winTEMP.BIN',
             'components/safe_browsing/content/resources/empty_file_type_policy.pb'}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class NativeChromiumBaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Uses the same Windows archive decoder as the runner')
    def test_full_archive_preserves_unicode_and_internal_aliases_without_linux_toolchains(self):
        sevenzip = shutil.which('7z.exe') or str(Path(os.environ['ProgramFiles']) / '7-Zip/7z.exe')
        name = 'chromium-158.0.8127.36'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'work'
            (work/'src').mkdir(parents=True)
            archive = work / 'download_cache' / (name + '.tar.xz')
            archive.parent.mkdir()
            settings = configparser.ConfigParser(interpolation=None)
            settings.read(ROOT/native.ARCHIVE, encoding='utf-8')
            pgo = settings['win64-pgo']
            profile = b'fixture for the Windows profile'
            save(archive.parent/pgo['download_filename'], profile)
            contents = {'chrome/VERSION':b'MAJOR=158\nMINOR=0\nBUILD=8127\nPATCH=36\n',
                        'chrome/build/win64.pgo.txt':(pgo['download_filename']+'\n').encode(),
                        'build/linux/sysroot_scripts/keep.py':b'keep',
                        'third_party/llvm-build-tools/keep/host.txt':b'host',
                        'third_party/test/' + chr(0x1f30b) + '.def':b'EXPORTS',
                        native.WINDOWS_LLVM_ROOT+'/bin/clang':b'linux'}
            contents.update({directory + '/usr/lib/example.so':b'linux' for directory in native.LINUX_SYSROOTS})
            with tarfile.open(archive,'w:xz',format=tarfile.PAX_FORMAT) as out:
                for alias, target in [('tools/alias','../third_party/test'), ('tools/file','../chrome/VERSION'),
                                      ('tools/also-file','file')]:
                    member=tarfile.TarInfo(name + '/' + alias)
                    member.type=tarfile.SYMTYPE;member.linkname=target;out.addfile(member)
                for path, data in contents.items():
                    item = tarfile.TarInfo(name + '/' + path)
                    item.size = len(data)
                    out.addfile(item, io.BytesIO(data))
            digest = native.digest(archive.read_bytes())
            policy = json.loads((ROOT/native.POLICY).read_bytes())
            config = json.loads((ROOT/'build/lite-windows158.json').read_bytes())
            original = policy['original_source_archive_sha256']
            policy['original_source_archive_sha256'] = digest
            packed = json.dumps(policy).encode()
            config['chromium_base_sha256'] = native.digest(packed)
            save(root/native.POLICY, packed)
            save(root/'build/lite-windows158.json', json.dumps(config).encode())
            ini = (ROOT/native.ARCHIVE).read_bytes().replace(original.encode(),digest.encode())
            save(root/native.ARCHIVE, ini.replace(pgo['sha256'].encode(), native.digest(profile).encode()))
            result = native.unpack_windows_source(root,work,sevenzip)
            self.assertEqual(result['archive_sha256'],digest)
            self.assertEqual(result['materialized_aliases'],3)
            self.assertEqual(result['win64_pgo_sha256'],native.digest(profile))
            installed = work/'src/chrome/build/pgo_profiles'/pgo['download_filename']
            self.assertEqual(installed.read_bytes(),profile)
            mtime = installed.stat().st_mtime_ns
            spec = native.source_policy(root)[1]['win64-pgo']
            self.assertEqual(native.install_win64_profile(work/'src',archive.parent,spec),native.digest(profile))
            self.assertEqual(installed.stat().st_mtime_ns,mtime)
            installed.unlink()
            save(archive.parent/pgo['download_filename'], b'corrupt')
            with self.assertRaisesRegex(ValueError, 'PGO profile digest mismatch'):
                native.install_win64_profile(work/'src',archive.parent,spec)
            self.assertFalse(installed.exists())
            self.assertEqual((work/'src/tools/alias'/(chr(0x1f30b)+'.def')).read_bytes(),b'EXPORTS')
            self.assertFalse((work/'src/tools/file').is_symlink())
            self.assertEqual((work/'src/tools/also-file').read_bytes(),contents['chrome/VERSION'])
            for path, data in contents.items():
                if data == b'linux':
                    self.assertFalse((work/'src'/path).exists())
                else:
                    self.assertEqual((work/'src'/path).read_bytes(),data)
            for directory in (*native.LINUX_SYSROOTS, native.WINDOWS_LLVM_ROOT):
                self.assertFalse((work/'src'/directory).exists())
            other = root / 'damaged'
            (other/'src').mkdir(parents=True)
            save(other/'download_cache'/archive.name,b'damaged')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                native.unpack_windows_source(root,other,sevenzip)
            self.assertEqual(list((other/'src').iterdir()),[])

    def test_active_native_inputs_and_functional_contract(self):
        product = lite_patch_stack.load(ROOT)
        policy = json.loads((ROOT / native.POLICY).read_bytes())
        resources = policy['preserve_resources']
        rows = {row['path']: row for row in product['source_files']}
        for directory in (*native.LINUX_SYSROOTS, native.WINDOWS_LLVM_ROOT):
            self.assertFalse(any(name == directory or name.startswith(directory+'/') for name in rows), directory)
        self.assertEqual(set(resources), RESOURCES)
        self.assertEqual(product['runtime']['restore_pruned_resources'], {})
        for path, spec in resources.items():
            self.assertEqual((rows[path]['before_sha256'], rows[path]['after_sha256']),
                             (spec['sha256'], spec['sha256']))
        self.assertEqual([policy['layers'][k]['patch_count'] for k in ('core','windows')],[110,25])
        self.assertEqual([len(policy['skip_patches'][k]) for k in ('core','windows')],[9,1])
        self.assertEqual(product['runtime']['security_restoration']['safe_browsing_mode'],1)
        self.assertNotIn('17-speech.patch',[Path(e['path']).name for e in product['patches']])
        self.assertEqual(rows['content/browser/speech/tts_win.cc']['before_sha256'],rows['content/browser/speech/tts_win.cc']['after_sha256'])

    def test_partial_sections_and_unpruned_resource_are_authenticated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root/'work'
            resource = 'path/native.bin'
            source_patch = '--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+new\n'
            config = json.loads((ROOT/native.POLICY).read_text('utf-8'))
            config.update(use_chromium_files=['a.cc'], skip_patches={'core':['skip.patch'],'windows':[]}, preserve_pruned_sources={},
                          preserve_resources={resource:{'sha256':native.digest(b'abc'),'size':3}}, layers={})
            for layer in ('core','windows'):
                folder=root/layer
                items={'mixed.patch':source_patch.format('a.cc')+source_patch.format('b.cc')}
                if layer=='core':
                    items['skip.patch']=source_patch.format('a.cc')
                for name,text in items.items():
                    save(folder/'patches'/name,text.encode())
                save(folder/'patches/series', ('\n'.join(items)+'\n').encode())
                pruning=(resource+'\npath/retired.bin\n').encode()
                save(folder/'pruning.list',pruning)
                keys=[(n,native.digest(t.encode())) for n,t in items.items()]
                config['layers'][layer]={'patch_count':len(items),
                    'patch_list_sha256':native.digest(json.dumps(keys,separators=(',',':')).encode()),
                    'pruning_sha256':native.digest(pruning)}
            raw=json.dumps(config).encode()
            save(root/native.POLICY,raw)
            save(root/'build/lite-windows158.json',json.dumps({'runtime':{'chromium_version':'158.0.8127.36'},
                                                              'chromium_base_sha256':native.digest(raw)}).encode())
            shutil.copy2(ROOT/native.ARCHIVE, root/native.ARCHIVE)
            save(work/'src'/resource,b'abc')
            receipt=native.render(root,work,root/'core',root/'windows')
            self.assertEqual(receipt['layers']['core']['omitted_patches'],['skip.patch'])
            self.assertEqual((work/'native158-base/windows/mixed.patch').read_text(),source_patch.format('b.cc'))
            self.assertEqual((work/'native158-base/pruning.list').read_text(),'path/retired.bin\n')
            before=(work/'native158-base/core/mixed.patch').stat().st_mtime_ns
            self.assertEqual(native.render(root,work,root/'core',root/'windows'),receipt)
            self.assertEqual((work/'native158-base/core/mixed.patch').stat().st_mtime_ns,before)
            save(work/'src'/resource,b'bad')
            with self.assertRaisesRegex(ValueError,'original archive'):
                native.render(root,work,root/'core',root/'windows')
            self.assertEqual((work/'src'/resource).read_bytes(),b'bad')
            save(root/'core/patches/mixed.patch',source_patch.format('c.cc').encode())
            with self.assertRaisesRegex(ValueError,'upstream patch inventory'):
                native.render(root,work,root/'core',root/'windows')

    def test_unexpected_base_file_is_rejected_before_product_patches(self):
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp); source=work/'src/new.cc'
            save(source,b'existing')
            manifest={'source_files':[{'path':'new.cc', 'before_sha256':None}]}
            with patch.object(lite_patch_stack,'load',return_value=manifest):
                with self.assertRaisesRegex(ValueError,'new.cc'):
                    native.verify_base(ROOT,work)


if __name__ == '__main__':
    unittest.main()
