"""Security-lane rejection tests use only owned temporary fixtures; never compile Chromium."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import security_build_record as record
from tools import security_checkpoint as checkpoint


class BuildRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'args.gn'
        self.good = '\n'.join(f'{k} = {v}' for k, v in record.REQUIRED_FLAGS.items()) + '\n'

    def inspect(self, text):
        self.path.write_bytes(text.encode('utf-8'))
        return record.inspect_gn(self.path)

    def test_exact_gn_bytes_and_safe_empty_placeholders(self):
        text = self.good + 'google_api_key = ""\ngoogle_default_client_secret = ""\n'
        result = self.inspect(text)
        self.assertEqual(result['content'], text)
        self.assertEqual(result['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse(result['credential_values_present'])

    def test_unsafe_release_flags_rejected(self):
        for key, bad in [('safe_browsing_mode','0'),('is_debug','true'),('is_component_build','true'),('is_official_build','false')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.inspect(self.good.replace(f'{key} = {record.REQUIRED_FLAGS[key]}',f'{key} = {bad}'))

    def test_secret_value_never_appears_in_rejection(self):
        secret = 'synthetic-do-not-log-value'
        for key in ['google_api_key', 'google_default_client_secret', 'google_default_client_id']:
            with self.subTest(key=key), self.assertRaises(ValueError) as result:
                self.inspect(self.good + f'{key} = "{secret}"\n')
            self.assertNotIn(secret, str(result.exception))

    def test_duplicate_or_expression_rejected(self):
        for suffix in ['safe_browsing_mode = 0\n', 'extra = getenv("SENSITIVE")\n']:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.inspect(self.good + suffix)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {**checkpoint.IDENTITY, 'repository':'owner/repo','run_id':'123',
            'source_commit':'a'*40,'stage':1,'recipe_sha256':'b'*64,'resume_safe':True,
            'gn_args_sha256':'c'*64,'source_receipt_sha256':'d'*64,
            'parts':[{'name':'tree.7z.001','bytes':4,'sha256':hashlib.sha256(b'abcd').hexdigest()}]}
        self.kwargs = dict(source_commit='a'*40,repository='owner/repo',source_run='123',expected_recipe='b'*64)

    def test_exact_manifest_admitted(self):
        self.assertEqual(checkpoint.validate_manifest(self.manifest,**self.kwargs),self.manifest['parts'])

    def test_wrong_base_variant_source_or_recipe_rejected(self):
        for key,bad in [('chromium_version','155.0.8059.39'),('build_variant','fingerprint-modes-win155'),
                        ('core_commit','e'*40),('windows_commit','e'*40),('source_commit','e'*40),
                        ('recipe_sha256','e'*64),('run_id','124'),('repository','foreign/repo'),('arch','arm64')]:
            m=copy.deepcopy(self.manifest);m[key]=bad
            with self.subTest(key=key),self.assertRaises(ValueError):checkpoint.validate_manifest(m,**self.kwargs)

    def test_diagnostic_only_partial_cannot_resume(self):
        m=copy.deepcopy(self.manifest);m['resume_safe']=False
        with self.assertRaisesRegex(ValueError,'diagnosis'):checkpoint.validate_manifest(m,**self.kwargs)

    def test_incomplete_or_unsafe_volumes_rejected(self):
        for parts in [[],[{'name':'../tree.7z.001','bytes':4,'sha256':'a'*64}],
                      [{'name':'tree.7z.002','bytes':4,'sha256':'a'*64}],
                      [{'name':'tree.7z.001','bytes':4}], self.manifest['parts']*2]:
            m=copy.deepcopy(self.manifest);m['parts']=parts
            with self.subTest(parts=parts),self.assertRaises(ValueError):checkpoint.validate_manifest(m,**self.kwargs)

    def test_same_size_volume_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'tree.7z.001';path.write_bytes(b'abcd')
            checkpoint.verify_volume(path,self.manifest['parts'][0])
            path.write_bytes(b'abce')
            with self.assertRaisesRegex(ValueError,'content mismatch'):checkpoint.verify_volume(path,self.manifest['parts'][0])

    def test_archive_paths_and_links_rejected(self):
        self.assertEqual(checkpoint.validate_archive_listing(['Path = C:/transfer/tree.7z.001','----------','Path = chromix/src/chrome/VERSION']),1)
        for value in ['Path = ../escape','Path = chromix/../escape','Path = C:/escape',
                      'Path = /escape','Path = other/file','Symbolic Link = target','Hard Link = target']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                checkpoint.validate_archive_listing(['----------',value])

    def test_extracted_source_and_config_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);(work/'src/out/Default').mkdir(parents=True);(work/'src/chrome').mkdir()
            (work/'src/chrome/VERSION').write_text('MAJOR=156\nMINOR=0\nBUILD=8078\nPATCH=79\n')
            (work/'src/out/Default/args.gn').write_bytes(b'gn')
            (work/'google-services-source.json').write_bytes(b'{}')
            m=copy.deepcopy(self.manifest)
            m['gn_args_sha256']=hashlib.sha256(b'gn').hexdigest();m['source_receipt_sha256']=hashlib.sha256(b'{}').hexdigest()
            checkpoint.verify_tree(work,m)
            (work/'src/chrome/VERSION').write_text('MAJOR=155\nMINOR=0\nBUILD=8059\nPATCH=39\n')
            with self.assertRaisesRegex(ValueError,'not Windows 156'):checkpoint.verify_tree(work,m)


class WorkflowTests(unittest.TestCase):
    def test_action_references_are_full_commits(self):
        import re
        root=Path(__file__).resolve().parents[2]
        for name in ['security-win156.yml','security-win156-worker.yml']:
            text=(root/'.github/workflows'/name).read_text()
            for action in re.findall(r'uses:\s*(\S+)',text):
                if action.startswith('./'):continue
                self.assertRegex(action,r'^[\w/-]+@[a-f0-9]{40}$')
        stage=(root/'build/windows/security-stage.ps1').read_text()
        self.assertLess(stage.index("'service-and-fingerprint-restore'"),stage.index("'gn-generate'"))
        self.assertLess(stage.index("'record-gn-before-compile'"),stage.index("Invoke-Owned 'ninja'"))
        self.assertIn('local builds are forbidden',stage)


@unittest.skipUnless(os.name == 'nt', 'Windows job-object behavior')
class OwnedProcessTests(unittest.TestCase):
    def test_nonzero_exit_is_not_accepted(self):
        from tools.security_run import run
        with tempfile.TemporaryDirectory() as tmp:
            result=run([sys.executable,'-c','raise SystemExit(7)'],tmp,10,Path(tmp)/'exit.log')
            self.assertEqual(result['exit_code'],7)
            self.assertFalse(result['timed_out'])
            self.assertFalse(result['cleanup_errors'])

    def test_timeout_ends_the_owned_process_tree(self):
        from tools.security_run import run
        with tempfile.TemporaryDirectory() as tmp:
            command=[sys.executable,'-c','import subprocess,sys,time;subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);time.sleep(60)']
            result=run(command,tmp,1,Path(tmp)/'timeout.log')
            self.assertTrue(result['timed_out'])
            self.assertFalse(result['cleanup_errors'])


if __name__=='__main__':unittest.main()
