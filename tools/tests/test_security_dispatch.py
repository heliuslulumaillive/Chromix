"""Security builds must never enter the historical Windows 155 lanes."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def document():
    return yaml.load((ROOT/'.github/workflows/build-google-services-win155.yml').read_text(), Loader=yaml.BaseLoader)


def test_security_has_explicit_dispatch_route():
    data=document(); inputs=data['on']['workflow_dispatch']['inputs']
    assert 'fingerprint-modes-win156' in inputs['build_variant']['options']
    assert {'fresh','resume','upstream','upgrade'} <= set(inputs['operation']['options'])
    job=data['jobs']['security-win156']
    assert job['needs']=='validate-inputs'
    assert "inputs.build_variant == 'fingerprint-modes-win156'" in job['if']
    assert job['uses']=='./.github/workflows/security-win156.yml'
    assert "inputs.operation == 'resume'" in job['with']['source_run']
    assert "inputs.operation == 'upgrade'" in job['with']['source_run']
    assert job['with']['source_stage'] == "${{ inputs.source_stage || '0' }}"
    assert job['with']['retain_checkpoint'] == '${{ inputs.retain_checkpoint }}'


def test_old_lanes_explicitly_exclude_security():
    jobs=document()['jobs']
    for name in ['verify-candidate','build-1','build-2','build-3','build-4','require-candidate']:
        assert "inputs.build_variant != 'fingerprint-modes-win156'" in jobs[name]['if']
    assert "inputs.build_variant == 'fingerprint-modes-win155'" in jobs['fingerprint-modes']['if']


def test_invalid_variant_operation_pairs_fail_before_any_build():
    jobs=document()['jobs']
    source=jobs['validate-inputs']['steps'][0]['run']
    assert "'fingerprint-modes-win156': {'fresh', 'resume', 'upstream', 'upgrade'}" in source
    assert "'google-services-self-use': {'verify', 'resume'}" in source
    for name in ['security-win156','fingerprint-modes','verify-candidate','build-1']:
        assert jobs[name]['needs']=='validate-inputs'
