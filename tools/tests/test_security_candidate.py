"""Variant/version and real CLI integration gates using synthetic owned artifacts."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
with mock.patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
    from tools import verify_google_services_candidate as candidate
    from tools import prepare_google_services as preparation
from tools import write_google_services_build_record as writer
from tools import security_build_record, verify_security_runtime
from tools.tests.test_fingerprint_build_pipeline import write_fingerprint_evidence, rebind_fingerprint_fixture

FP156 = 'fingerprint-modes-win156'
V156 = '156.0.8078.79'


def native_report():
    return {'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in candidate.NATIVE_PASSKEY_TESTS}]}


def write_security_evidence(root, bundle):
    """Synthetic protocol fixture: no browser, profile, network or installed dependencies."""
    source_path=bundle/'google-services-source.json'
    if not source_path.exists():
        source_path.write_text(json.dumps({'variant':'google-services-self-use',
            'build_variant':FP156,'chromium_version':V156}))
    build_path=bundle/'google-services-build.json'
    build=json.loads(build_path.read_text()) if build_path.exists() else {
        'build_variant':FP156,'chromium_version':V156,'source_commit':'a'*40,'run_id':50}
    content='\n'.join(f'{key} = {value}' for key,value in security_build_record.REQUIRED_FLAGS.items())+'\n'
    gn_sha=hashlib.sha256(content.encode()).hexdigest()
    source={'schema_version':1,'build_variant':'security-win156','payload_build_variant':FP156,
        'chromium_version':V156,'core_commit':security_build_record.CORE,'windows_commit':security_build_record.WINDOWS,
        'source_commit':build['source_commit'],'run_id':str(build['run_id']),
        'recipe_sha256':security_build_record.recipe(ROOT),'source_receipt_sha256':candidate.sha256(source_path),
        'gn':{'sha256':gn_sha,'bytes':len(content.encode()),'content':content,
              'assignments':dict(security_build_record.REQUIRED_FLAGS)}}
    source_record=root/'build-inputs.json';source_record.write_text(json.dumps(source))
    build['gn_args_sha256']=gn_sha;build_path.write_text(json.dumps(build))
    report={'schema_version':1,'status':'passed','browser_version':'Chrome/'+V156,
        'preferences_changed':False,'trust_store_changed':False,
        'checks':[{'name':name,'passed':True} for name in sorted(verify_security_runtime.REQUIRED_CHECKS)],
        'binding':{'browser_sha256':candidate.sha256(bundle/'chrome.exe'),'chrome_dll_sha256':candidate.sha256(bundle/'chrome.dll'),
            'verifier_sha256':candidate.sha256(ROOT/'tools/verify_security_runtime.py'),
            'recipe_tool_sha256':candidate.sha256(ROOT/'tools/security_build_record.py'),
            'requirements_sha256':candidate.sha256(ROOT/'tools/google-services-requirements.txt'),
            'python_sha256':'e'*64,'dependencies':{
                'playwright':{'version':'1.53.0','files':1,'inventory_sha256':'d'*64},
                'psutil':{'version':'7.2.2','files':1,'inventory_sha256':'c'*64}}},
        'source':verify_security_runtime.source_identity(source_record,source['recipe_sha256']),
        'pages':{name:{'url':url,'ready':True,'safeBrowsingSectionVisible':True}
                 for name,url in verify_security_runtime.URLS.items()},
        'preferences':{'available':True,'values':{key:{'error':None,'pref':{'key':key,'type':'BOOLEAN','value':value}}
                       for key,value in verify_security_runtime.EXPECTED_PREFS.items()}}}
    report_path=root/'security-runtime.json';report_path.write_text(json.dumps(report))
    return report_path,source_record,report


class SecurityCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        (self.bundle / 'chrome.exe').write_bytes(b'synthetic browser, never executable')
        (self.bundle / 'chrome.dll').write_bytes(b'synthetic library')

    def test_version_map_is_closed_and_artifact_names_are_variant_bound(self):
        source = {'repository':'fixture/repo','run_id':1,'artifact_id':2,'head_sha':'a'*40,
                  'artifact_name':'security-win156-x64'}
        candidate.validate_source_run(source,build_variant=FP156)
        for variant in ['google-services','fingerprint-modes-win155','unknown']:
            with self.subTest(variant=variant),self.assertRaises(ValueError):
                candidate.validate_source_run(source,build_variant=variant)
        with self.assertRaises(ValueError):candidate.variant_version('fingerprint-modes-win157')
        self.assertEqual(candidate.variant_version(FP156),V156)

    def test_writer_accepts_only_exact_pairs(self):
        for variant,version in [(None,'155.0.8059.39'),('fingerprint-modes-win155','155.0.8059.39'),(FP156,V156)]:
            source={'variant':'google-services-self-use','chromium_version':version}
            if variant:source['build_variant']=variant
            writer.validate_source_pair(source)
        for variant,version in [(None,V156),('fingerprint-modes-win155',V156),(FP156,'155.0.8059.39'),
                                (FP156,'156.0.8078.73'),('fingerprint-modes-win157',V156)]:
            source={'variant':'google-services-self-use','chromium_version':version}
            if variant:source['build_variant']=variant
            with self.subTest(source=source),self.assertRaises(ValueError):writer.validate_source_pair(source)
        for invalid in [None, [], {}]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                writer.validate_source_pair({'variant':'google-services-self-use',
                    'chromium_version':'155.0.8059.39','build_variant':invalid})

    def test_writer_cli_preserves_156_identity_and_immutable_inventory(self):
        work=self.root/'work';work.mkdir()
        source={'variant':'google-services-self-use','build_variant':FP156,'chromium_version':V156,
                'patch_sha256':'a'*64,'preparation_sha256':'b'*64}
        (work/'google-services-source.json').write_text(json.dumps(source))
        args=self.root/'args.gn';args.write_text('is_debug = false\n')
        env={**os.environ,'GITHUB_SHA':'c'*40,'GITHUB_RUN_ID':'42','GITHUB_REPOSITORY':'fixture/repo'}
        command=[sys.executable,'-B',str(ROOT/'tools/write_google_services_build_record.py'),
                 '--work',str(work),'--bundle',str(self.bundle),'--gn-args',str(args)]
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads((self.bundle/'google-services-build.json').read_text())
        self.assertEqual((receipt['build_variant'],receipt['chromium_version']),(FP156,V156))
        self.assertEqual(receipt['files']['chrome.exe'],candidate.sha256(self.bundle/'chrome.exe'))

    def test_candidate_upload_has_one_flat_dist_root(self):
        import yaml
        worker=yaml.safe_load((ROOT/'.github/workflows/security-win156-worker.yml').read_text())
        step=next(s for s in worker['jobs']['build']['steps'] if s.get('id')=='candidate')
        paths=step['with']['path'].splitlines()
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith('C:/c/dist/') for p in paths))
        self.assertIn('C:/c/dist/*.json',paths)

    def test_fingerprint_receipts_are_valid_for_only_the_declared_variant_and_version(self):
        for variant in ['fingerprint-modes-win155',FP156]:
            with self.subTest(variant=variant):
                report=write_fingerprint_evidence(self.root,self.bundle,build_variant=variant)
                candidate.validate_fingerprint_tests(report,self.bundle,build_variant=variant)
                wrong=FP156 if variant=='fingerprint-modes-win155' else 'fingerprint-modes-win155'
                with self.assertRaisesRegex(ValueError,'capabilities'):
                    candidate.validate_fingerprint_tests(report,self.bundle,build_variant=wrong)
        caps=json.loads((self.root/'fingerprint-modes-capabilities.json').read_text())
        caps['chromium_version']='156.0.8078.73'
        (self.root/'fingerprint-modes-capabilities.json').write_text(json.dumps(caps))
        (self.bundle/'fingerprint-modes-capabilities.json').write_text(json.dumps(caps))
        rebind_fingerprint_fixture(self.root)
        with self.assertRaisesRegex(ValueError,'capabilities'):
            candidate.validate_fingerprint_tests(report,self.bundle,build_variant=FP156)

    def test_native_and_fingerprint_cli_modes_execute_separately(self):
        native=self.root/'native.json';native.write_text(json.dumps(native_report()))
        report=write_fingerprint_evidence(self.root,self.bundle,build_variant=FP156)
        command=[sys.executable,'-B','-X','utf8',str(ROOT/'tools/verify_google_services_candidate.py'),
                 '--build-variant',FP156]
        for args in [['--native-tests-json',str(native)],
                     ['--fingerprint-tests-json',str(report),'--bundle',str(self.bundle)]]:
            result=subprocess.run(command+args,capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
        joint=subprocess.run(command+['--native-tests-json',str(native),'--fingerprint-tests-json',str(report),
                                      '--bundle',str(self.bundle)],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertNotEqual(joint.returncode,0)
        self.assertIn('not allowed with argument',joint.stderr)

    def test_156_source_recipe_binds_security_inputs_and_rejects_wrong_pair(self):
        digest,inputs=preparation.recipe_identity(ROOT)
        receipt={'variant':'google-services-self-use','build_variant':FP156,'chromium_version':V156,
                 'patch_sha256':candidate.sha256(ROOT/'patches/google-services/0001-restore-google-services.patch'),
                 'preparation_sha256':digest,'preparation_inputs':inputs}
        candidate.validate_source_recipe(receipt,copy.deepcopy(receipt),build_variant=FP156)
        for key,value in [('chromium_version','155.0.8059.39'),('build_variant','fingerprint-modes-win155'),
                          ('preparation_sha256','0'*64)]:
            changed=copy.deepcopy(receipt);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                candidate.validate_source_recipe(changed,copy.deepcopy(changed),build_variant=FP156)
        for name in ['build/args.security.gn','tools/restored_source_resources.py']:
            changed=copy.deepcopy(receipt);changed['preparation_inputs'].pop(name)
            with self.subTest(name=name),self.assertRaises(ValueError):
                candidate.validate_source_recipe(changed,copy.deepcopy(changed),build_variant=FP156)

    def test_complete_156_artifact_uses_exact_runtime_version_and_keeps_native_coverage(self):
        digest,inputs=preparation.recipe_identity(ROOT)
        receipt={'variant':'google-services-self-use','build_variant':FP156,'chromium_version':V156,
            'patch_sha256':candidate.sha256(ROOT/'patches/google-services/0001-restore-google-services.patch'),
            'preparation_sha256':digest,'preparation_inputs':inputs}
        for path in [self.root/'google-services-source.json',self.bundle/'google-services-source.json']:
            path.write_text(json.dumps(receipt))
        write_fingerprint_evidence(self.root,self.bundle,build_variant=FP156)
        (self.root/'native-passkey-unittests.json').write_text(json.dumps(native_report()))
        archive=self.root/'chromix-win-x64.zip';archive.write_bytes(b'synthetic archive; extraction is mocked')
        source={'repository':'fixture/repo','head_sha':'a'*40,'run_id':50,'artifact_id':51,'artifact_name':'security-win156-x64'}
        build={'build_variant':FP156,'chromium_version':V156,'repository':source['repository'],
            'source_commit':source['head_sha'],'run_id':50,'patch_sha256':receipt['patch_sha256'],
            'preparation_sha256':digest,'files':candidate.inventory(self.bundle)}
        build_path=self.bundle/'google-services-build.json';build_path.write_text(json.dumps(build))
        security_path,_,_=write_security_evidence(self.root,self.bundle)
        build=json.loads(build_path.read_text())
        reports=self.root/'reports';reports.mkdir()
        bundle_report={'bundle_dir':str(self.bundle),'archive':{'sha256':candidate.sha256(archive)}}
        def runtime(command,**kwargs):
            payload={name:candidate.sha256(self.bundle/name) for name in ['chrome.exe','chrome.dll']}
            probe={'status':'passed','browser_sha256':payload['chrome.exe'],'chrome_dll_sha256':payload['chrome.dll'],
                   'checks':[{'name':name,'passed':True} for name in candidate.REQUIRED_CHECKS]}
            (reports/'google-webauthn.json').write_text(json.dumps(probe))
            return {'exit_code':0,'timed_out':False,'cleanup_errors':[]}
        with mock.patch.object(candidate,'extract_archive',return_value=bundle_report),\
             mock.patch.object(candidate,'runtime_smoke',return_value={}) as smoke,\
             mock.patch.object(candidate,'run_command',side_effect=runtime):
            result={};candidate.verify_candidate(self.root,source,reports,result,build_variant=FP156)
            self.assertEqual(result['status'],'passed')
            self.assertEqual(smoke.call_args.args[1],V156)
            self.assertEqual(set(result['native_passkey_regression_tests']),candidate.NATIVE_PASSKEY_TESTS)
            self.assertEqual(result['security_runtime']['status'],'passed')
            security_bytes=security_path.read_bytes();security_path.unlink()
            with self.assertRaisesRegex(ValueError,'Mandatory'):
                candidate.verify_candidate(self.root,source,reports,{},build_variant=FP156)
            security_path.write_bytes(security_bytes)
            build['chromium_version']='155.0.8059.39';build_path.write_text(json.dumps(build))
            with self.assertRaisesRegex(ValueError,'build receipt'):
                candidate.verify_candidate(self.root,source,reports,{},build_variant=FP156)

    def test_security_report_requires_unique_complete_strict_boolean_checks(self):
        path,record,valid=write_security_evidence(self.root,self.bundle)
        candidate.validate_security_runtime(path,record,self.bundle)
        mutations=[lambda d:d['checks'].pop(),lambda d:d['checks'].__setitem__(0,d['checks'][-1]),
            lambda d:d['checks'].append({'name':'unrequested','passed':True}),
            lambda d:d['checks'][0].update(passed=1),lambda d:d['checks'][0].update(passed='true')]
        for mutate in mutations:
            changed=copy.deepcopy(valid);mutate(changed);path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):candidate.validate_security_runtime(path,record,self.bundle)

    def test_security_report_rejects_old_payload_and_changed_source_tool_bindings(self):
        path,record,valid=write_security_evidence(self.root,self.bundle)
        for section,key in [('binding','browser_sha256'),('binding','chrome_dll_sha256'),
                            ('binding','verifier_sha256'),('binding','recipe_tool_sha256'),
                            ('binding','requirements_sha256'),('source','record_sha256'),
                            ('source','recipe_sha256'),('source','source_receipt_sha256'),('source','gn_sha256')]:
            changed=copy.deepcopy(valid);changed[section][key]='0'*64;path.write_text(json.dumps(changed))
            with self.subTest(section=section,key=key),self.assertRaises(ValueError):
                candidate.validate_security_runtime(path,record,self.bundle)
        path.write_text(json.dumps(valid));record.write_text(record.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'source-record SHA'):
            candidate.validate_security_runtime(path,record,self.bundle)

    def test_security_raw_pref_and_rebound_source_commit_must_agree(self):
        path,record,valid=write_security_evidence(self.root,self.bundle)
        changed=copy.deepcopy(valid)
        changed['preferences']['values']['safebrowsing.enabled']['pref']['value']=False
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError,'preference evidence'):
            candidate.validate_security_runtime(path,record,self.bundle)
        source=json.loads(record.read_text());source['source_commit']='b'*40;record.write_text(json.dumps(source))
        valid['source']=verify_security_runtime.source_identity(record,source['recipe_sha256'])
        path.write_text(json.dumps(valid))
        with self.assertRaisesRegex(ValueError,'packaged source/build'):
            candidate.validate_security_runtime(path,record,self.bundle)

    def test_security_cli_is_156_only_and_stage_gate_precedes_archive(self):
        path,record,_=write_security_evidence(self.root,self.bundle)
        command=[sys.executable,'-B','-X','utf8',str(ROOT/'tools/verify_google_services_candidate.py'),
                 '--security-tests-json',str(path),'--source-record',str(record),'--bundle',str(self.bundle)]
        result=subprocess.run(command+['--build-variant',FP156],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        wrong=subprocess.run(command+['--build-variant','fingerprint-modes-win155'],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertNotEqual(wrong.returncode,0)
        stage=(ROOT/'build/windows/security-stage.ps1').read_text()
        self.assertLess(stage.index("Invoke-Owned 'security-runtime'"),stage.index("Invoke-Owned 'accept-security-runtime'"))
        self.assertLess(stage.index("Invoke-Owned 'accept-security-runtime'"),stage.index("Invoke-Owned 'archive'"))
        self.assertIn("'security-runtime.json','fingerprint-modes-validation.json'",stage)


if __name__=='__main__':unittest.main()
