"""Invented coverage fixtures only; no physical corpus is supplied here."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gpu_backend_audit as audit
import gpu_device_matrix as matrix
import fingerprint_acceptance as acceptance
from chromix import device_pool as pool, _device_render as render
from chromix._device_probe import probe_hash
from gpu_backend_fixtures import backend_fixture, system_fixture
from test_device_render import bundle, pack, refresh_render

NOW=datetime(2026,9,12,tzinfo=timezone.utc)


def dump(path,value):
    path.write_text(json.dumps(value),encoding='utf-8')


def audit_fixture():
    host={'test_only':'not a hardware record'}
    return {'schema_version':1,'browser_sha256':'a'*64,'browser_version':'152.0.7977.82',
        'probe_sha256':probe_hash(),'status':'passed','errors':[],'gaps':[],
        'host_before':host,'host_after':deepcopy(host),'runs':[
            {'profile':profile,'launch_args':audit.arguments(i),'gpu_system':system_fixture(),
             'observation':{s:backend_fixture(s) for s in pool.SCOPES}} for i,profile in enumerate(audit.PROFILES)]}


def test_native_policy_audit_uses_conflicts_without_redundant_disable_flags():
    assert '--uxr-gpu-backend=native' in audit.arguments(2)
    assert all(x not in audit.arguments(2) for x in ('--fingerprint=off','--uxr-disable-fingerprint-noise','--uxr-webgl-real'))
    assert '--uxr-synthetic-device-tests=true' in audit.arguments(2)
    assert audit.assess(audit_fixture()) == ([],[])


@pytest.mark.parametrize('fault',('missing','policy','gpu','raw','host','probe','forged_success'))
def test_audit_and_acceptance_gate_recheck_raw_gpu_contracts(fault):
    value=audit_fixture()
    if fault=='missing': value['runs'].pop()
    elif fault=='policy': value['runs'][2]['launch_args'].remove('--uxr-gpu-backend=native')
    elif fault=='gpu': del value['runs'][0]['gpu_system']
    elif fault=='raw': value['runs'][1]['observation']['worker']['webgpu'][0]['value']['formats'][0]['bytes'][0]=0
    elif fault=='host': value['host_after']={'changed':True}
    elif fault=='probe': value['probe_sha256']='b'*64
    else: value['runs'][0]['observation']['window']={'status':'passed'}
    assert audit.assess(value)[0]
    assert acceptance.assess_suite('gpu_backend',value,'a'*64,'152.0.7977.82')[0]


def test_optional_fallback_stays_incomplete_not_fabricated_coverage():
    value=audit_fixture()
    for run in value['runs']:
        for observation in run['observation'].values():
            observation['webgpu'][3]={'request':'fallback','status':'observed','value':{'available':False,'reason':'native null'}}
    errors,gaps=audit.assess(value)
    assert not errors and len(gaps)==15


@pytest.fixture
def reviewed(tmp_path):
    root=tmp_path/'unit-device';record=bundle(root)
    host=deepcopy(record['device']['host']);host['os'].update(system='Windows',architecture='AMD64')
    host['gpu']={'status':'observed','source':'Win32_VideoController','value':[
        {'Name':'unit fixture NVIDIA','PNPDeviceID':'PCI\\VEN_10DE&DEV_1234\\fixture','DriverVersion':'1'},
        {'Name':'unit fixture AMD (not exercised)','PNPDeviceID':'PCI\\VEN_1002&DEV_5678\\fixture','DriverVersion':'2'},
        {'Name':'unit Virtual Display','PNPDeviceID':'ROOT\\DISPLAY\\fixture','DriverVersion':'3'}]}
    record['device']['host']=host;dump(root/'host.json',host)
    record['evidence']['host']['sha256']=pool.file_hash(root/'host.json')
    browser=pool.load_json(root/'browser.json')
    for observation in browser['observations']:
        for scope in pool.SCOPES:
            raw=render.unpack(observation[scope]['render']);gpu=raw['gpuBackend']
            for api in ('webgl','webgl2'):
                gl=gpu['webgl'][api]['value']
                for identity in (gl['beforeIdentity'],gl['lifecycle']['afterIdentity']):
                    identity.update(vendor='Google Inc. (NVIDIA)',renderer='fixture NVIDIA, not hardware')
            for row in gpu['webgpu']:
                row['value']['identity']['vendor']='nvidia'
                row['value']['lifecycle']['afterIdentity']['vendor']='nvidia'
            observation[scope]['render']=pack(raw)
    refresh_render(record,root,browser);dump(root/'record.json',record)
    manifest={'schema_version':1,'browser_version':'152.0.7977.82','probe_sha256':probe_hash(),'max_age_days':30,
        'cohorts':[{'id':'test','os':'Windows','architecture':'AMD64','min_devices':1}],
        'samples':[{'path':'unit-device/record.json','record_id':record['record_id'],
            'record_sha256':pool.file_hash(root/'record.json'),'device_id':'invented-test-tag','cohort_id':'test',
            'reviewer':'unit-test-not-authenticated','reviewed_at':'2026-09-11T00:00:00Z','expires_at':'2026-10-01T00:00:00Z',
            # This exercises the supplied review-metadata branch; never a real claim.
            'sample_kind':'physical'}]}
    review_path=tmp_path/'review.json';dump(review_path,manifest)
    cells=[dict(id='unit-'+api,os='Windows',architecture='x64',api=api,vendor_family='nvidia',min_devices=1)
           for api in ('webgl','webgl2','webgpu')]
    cells.append(dict(id='unexercised-amd',os='Windows',architecture='x64',api='webgpu',vendor_family='amd',min_devices=1))
    plan_path=tmp_path/'plan.json';dump(plan_path,{'schema_version':1,'cells':cells})
    return plan_path,review_path,manifest


def test_only_reviewed_exercised_vendor_cells_count_and_hybrid_inventory_is_not_duplicated(reviewed):
    plan,review,_=reviewed
    result=matrix.build_matrix(plan,[review],now=NOW)
    assert result['errors']==[],result['errors']
    assert [c['observed_devices'] for c in result['cells']]==[1,1,1,0]
    assert result['unique_reviewed_devices']==1
    assert result['cells'][-1]['status']=='not_sampled' and result['status']=='incomplete'
    assert result['qualification']['physical_attestation'] is False
    assert result['qualification']['exact_adapter_binding'] is False


@pytest.mark.parametrize('kind',('virtual','fixture','control'))
def test_nonphysical_samples_cannot_supply_a_cell(reviewed,kind):
    plan,review,manifest=reviewed
    manifest['samples'][0]['sample_kind']=kind;dump(review,manifest)
    result=matrix.build_matrix(plan,[review],now=NOW)
    assert result['status']=='failed'
    assert all(c['observed_devices']==0 for c in result['cells'])


def test_duplicate_review_cannot_inflate_distinct_device_counts(reviewed):
    plan,review,_=reviewed
    result=matrix.build_matrix(plan,[review,review],now=NOW)
    assert result['status']=='failed' and result['unique_reviewed_devices']==1
    assert all(c['observed_devices']<=1 for c in result['cells'])


def test_changed_review_does_not_publish_partial_device_or_cell_counts(reviewed,monkeypatch):
    plan,review,_=reviewed
    original=matrix.exercised
    def changed(observation):
        # This runs after independent corpus validation, during matrix work.
        review.write_bytes(review.read_bytes()+b' ')
        return original(observation)
    monkeypatch.setattr(matrix,'exercised',changed)
    result=matrix.build_matrix(plan,[review],now=NOW)
    assert result['status']=='failed' and result['unique_reviewed_devices']==0
    assert all(c['observed_devices']==0 and c['observed_scopes']==[] for c in result['cells'])


def test_control_can_be_diagnostic_but_never_counts_even_if_oracle_passes(tmp_path):
    plan=tmp_path/'plan.json';dump(plan,{'schema_version':1,'cells':[dict(id='unit',os='Windows',architecture='x64',api='webgpu',vendor_family='nvidia',min_devices=1)]})
    control=tmp_path/'control.json';dump(control,audit_fixture())
    result=matrix.build_matrix(plan,control_paths=[control])
    assert result['status']=='incomplete' and result['unique_reviewed_devices']==0
    assert result['controls'][0]['status']=='excluded' and result['cells'][0]['status']=='not_sampled'


def test_committed_matrix_is_an_unsampled_plan_not_a_fabricated_corpus():
    plan=Path(__file__).with_name('fixtures')/'gpu-device-matrix.json'
    result=matrix.build_matrix(plan)
    assert result['status']=='incomplete' and len(result['cells'])==33
    assert all(c['status']=='not_sampled' and c['observed_devices']==0 for c in result['cells'])
    assert result['controls']==[] and result['reviews']==[]


@pytest.mark.parametrize('identity,api',[
    ({'vendor':'Google Inc. (NVIDIA)','renderer':'SwiftShader'},'webgl'),
    ({'vendor':'nvidia','isFallbackAdapter':True},'webgpu'),
    ({'vendor':'nvidia','isFallbackAdapter':None},'webgpu'),
    ({'vendor':'Intel NVIDIA','renderer':''},'webgl'),
    ({'vendor':'NVIDIA','renderer':'VMware virtual GPU'},'webgl'),
    ({'vendor':'unknown','renderer':''},'webgl')])
def test_software_ambiguous_and_opaque_identities_are_not_physical_vendor_bindings(identity,api):
    assert matrix.family_hint(identity,api) is None


def test_matrix_cli_preserves_new_diagnostics_and_refuses_clobber(tmp_path):
    plan=Path(__file__).with_name('fixtures')/'gpu-device-matrix.json';out=tmp_path/'report.json'
    args=['--matrix',str(plan),'--output',str(out)]
    assert matrix.main(args)==1
    before=out.read_bytes()
    with pytest.raises(SystemExit):matrix.main(args)
    assert out.read_bytes()==before
