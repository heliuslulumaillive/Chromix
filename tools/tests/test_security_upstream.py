"""Exact Windows 156 checkpoint admission; no network or large cache download."""
import copy
from datetime import datetime, timezone
import unittest
from tools import fetch_upstream_cache as cache
from tools.tests.test_fetch_upstream_cache import metadata


class Windows156UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.pin, self.identity = cache.load_manifest('windows', 'x64', 41098495420)
        self.run, self.artifact = metadata(self.pin)
        self.run.update(status='in_progress', conclusion=None, run_attempt=1,
                        run_started_at='2026-10-30T17:24:06Z')
        self.artifact['created_at'] = '2026-10-31T02:24:28Z'
        self.producer = {'id':self.pin['producer_job_id'], 'name':self.pin['producer_job_name'],
            'run_id':self.pin['run_id'], 'run_attempt':1, 'head_sha':self.pin['head_sha'],
            'head_branch':self.pin['head_branch'], 'workflow_name':'build-x64',
            'status':'completed','conclusion':'success','started_at':'2026-10-30T21:00:00Z',
            'completed_at':'2026-10-31T02:25:30Z','steps':[{'name':'Run Stage',
                'status':'completed','conclusion':'success','started_at':'2026-10-30T21:05:00Z',
                'completed_at':'2026-10-31T02:25:00Z'}]}
        self.now=datetime(2026,10,31,3,tzinfo=timezone.utc)

    def validate(self, run=None, artifact=None, producer=None, pin=None):
        cache.validate_metadata(pin or self.pin,run or self.run,artifact or self.artifact,
            self.now,producer=self.producer if producer is None else producer)

    def test_current_manifest_matches_exact_156_identity(self):
        self.assertEqual(self.pin['chromium_version'],'156.0.8078.79')
        self.assertEqual(self.pin['ungoogled_commit'],'28a211ab4ab6b83d919966a4f58a6df23cf4fb29')
        self.assertEqual(self.pin['head_sha'],'8492884eb30fdd9417ea7816aa92def805c97c74')
        self.assertEqual(self.pin['artifact']['id'],13026019178)
        self.assertEqual(self.pin['artifact']['digest'],'sha256:91907139b2379a922274807ec66b66fb427be525b22f66248cc2086e58041982')
        self.assertEqual(self.identity['producer_job_id'],124333249961)
        self.validate()

    def test_old_versions_and_wrong_runs_cannot_be_selected(self):
        for run in [40692824400,40811853097,41015794414,40699893733]:
            with self.subTest(run=run),self.assertRaisesRegex(cache.CacheMiss,'run_id_mismatch'):
                cache.load_manifest('windows','x64',run)
        for version in ['155.0.8059.39','156.0.8078.73']:
            pin=copy.deepcopy(self.pin);pin['chromium_version']=version
            with self.subTest(version=version),self.assertRaisesRegex(cache.CacheMiss,'untrusted_checkpoint'):
                self.validate(pin=pin)

    def test_exact_successful_producer_is_mandatory(self):
        for key,value in [('id',124333249962),('head_sha','0'*40),('run_attempt',2),
                          ('workflow_name','build-arm'),('name','build / build-1'),
                          ('status','in_progress'),('conclusion','failure')]:
            producer=copy.deepcopy(self.producer);producer[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(cache.CacheMiss,'untrusted_producer'):
                self.validate(producer=producer)
        with self.assertRaisesRegex(cache.CacheMiss,'untrusted_producer'):
            self.validate(producer={})

    def test_artifact_digest_size_provenance_and_expiration_are_mandatory(self):
        for key,value in [('id',13026019179),('digest','sha256:'+'0'*64),('size_in_bytes',1),
                          ('expired',True),('expires_at','2026-10-30T00:00:00Z')]:
            artifact=copy.deepcopy(self.artifact);artifact[key]=value
            with self.subTest(key=key),self.assertRaises(cache.CacheMiss):self.validate(artifact=artifact)
        artifact=copy.deepcopy(self.artifact);artifact['workflow_run']['head_sha']='0'*40
        with self.assertRaisesRegex(cache.CacheMiss,'artifact_provenance'):self.validate(artifact=artifact)

    def test_artifact_must_be_created_in_the_successful_stage(self):
        artifact=copy.deepcopy(self.artifact);artifact['created_at']='2026-10-31T02:26:00Z'
        with self.assertRaisesRegex(cache.CacheMiss,'artifact_creation_window'):self.validate(artifact=artifact)
        producer=copy.deepcopy(self.producer);producer['steps'][0]['conclusion']='failure'
        with self.assertRaisesRegex(cache.CacheMiss,'untrusted_producer_steps'):self.validate(producer=producer)

    def test_failed_run_and_wrong_attempt_rejected_even_with_successful_old_job(self):
        for change in [{'status':'completed','conclusion':'failure'},{'run_attempt':2}]:
            with self.subTest(change=change),self.assertRaises(cache.CacheMiss):self.validate(run={**self.run,**change})


if __name__=='__main__':unittest.main()
