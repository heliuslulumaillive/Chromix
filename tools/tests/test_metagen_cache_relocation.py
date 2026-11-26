"""An exact cache move restores tool identity without touching native objects."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import hashlib
from tools import relocate_metagen_cache as repair
from tools import install_windows_metagen_bindings as bindings
from tools import prepare_restored_build as prepare

FILES={'__init__.py':b'# cached initialization\n','cindex.py':b'# cached exact bindings\n'}

class CacheRelocationTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cache-relocation-');self.addCleanup(temporary.cleanup)
        self.src=Path(temporary.name).resolve()/'src';self.src.mkdir()
        self.compiler=self.put(prepare.CLANG/'bin/clang-cl.exe',b'unchanged compiler bytes')
        self.put(prepare.RUST/'bin/rustc.exe',b'unchanged rust compiler')
        self.obj=self.put(Path('out/Default/obj/keep.obj'),b'existing native object')
        self.gn=self.put(Path('out/Default/gn.exe'),b'existing GN')
        self.base=prepare.tool_fingerprint(self.src,'windows','x64')
        self.put(Path(prepare.MARKER),(json.dumps({'tool_fingerprint':self.base})+'\n').encode())
        self.legacy=self.src/bindings.SOURCE_PACKAGES[0]
        for name,content in FILES.items():self.put(Path(bindings.SOURCE_PACKAGES[0])/name,content)
        self.with_cache=prepare.tool_fingerprint(self.src,'windows','x64')
        for name,value in [('BASE',self.base),('WITH_CACHE',self.with_cache)]:
            patch=mock.patch.object(repair,name,value);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(bindings,'HASHES',{name:hashlib.sha256(data).hexdigest() for name,data in FILES.items()});patch.start();self.addCleanup(patch.stop)

    def put(self,name,data):
        file=self.src/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data);return file

    def snapshot(self):
        return {p.relative_to(self.src).as_posix():(p.read_bytes(),p.stat().st_mtime_ns) for p in self.src.rglob('*') if p.is_file()}

    def test_exact_move_preserves_objects_gn_and_full_original_fingerprints(self):
        old={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in [self.obj,self.gn,self.compiler,self.src/prepare.MARKER]}
        report=repair.relocate(self.src,dict(repair.DONOR))
        self.assertFalse(report['compiled_outputs_touched'])
        self.assertEqual(prepare.tool_fingerprint(self.src,'windows','x64'),self.base)
        self.assertEqual(bindings.read_package(bindings.retained_package(self.src)),FILES)
        self.assertFalse(self.legacy.exists())
        for p,expected in old.items():self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),expected)
        self.assertEqual(repair.relocate(self.src,dict(repair.DONOR))['status'],'already-relocated')

    def test_any_other_tool_change_is_rejected_without_cache_or_object_writes(self):
        self.compiler.write_bytes(b'unreviewed compiler change')
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'Compiler tree differs'):repair.relocate(self.src,dict(repair.DONOR))
        self.assertEqual(self.snapshot(),before)

    def test_wrong_donor_or_cache_bytes_are_rejected_without_writes(self):
        for key,value in [('stage',2),('source_commit','0'*40),('recipe_sha256','0'*64)]:
            before=self.snapshot();donor=dict(repair.DONOR);donor[key]=value
            with self.assertRaises(ValueError):repair.relocate(self.src,donor)
            self.assertEqual(self.snapshot(),before)
        (self.legacy/'cindex.py').write_bytes(b'changed bindings')
        before=self.snapshot()
        with self.assertRaises(ValueError):repair.relocate(self.src,dict(repair.DONOR))
        self.assertEqual(self.snapshot(),before)

    def test_restoration_finishes_before_gn_is_bootstrapped(self):
        script=(Path(__file__).resolve().parents[2]/'build/windows/security-stage.ps1').read_text('utf8')
        configure=script.split("    'Configure' {",1)[1].split("    'Compile' {",1)[0]
        self.assertLess(configure.index('Select-WindowsRustCompiler'),configure.index("Invoke-Owned 'gn-bootstrap'"))
        self.assertLess(configure.index("Invoke-Owned 'metagen-configure'"),configure.index("Invoke-Owned 'gn-generate'"))
        self.assertLess(configure.index("Invoke-Owned 'gn-generate'"),configure.index("Invoke-Owned 'metagen-verify'"))
        self.assertLess(configure.index("Invoke-Owned 'metagen-verify'"),configure.index("Invoke-Owned 'pending-actions'"))

if __name__=='__main__':unittest.main()
