"""Explicit byte preservation and strictly bounded unchanged-input reuse."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from justbuild.cad import generate
from synthetic_fixture import make_fixture


def run(root,r,cache=None):
    root.mkdir(parents=True,exist_ok=True)
    p=root/'request.json';p.write_text(json.dumps(r));rp=generate(p,root/'generated',source_validation_cache=cache)
    return rp,json.loads(rp.read_text())


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    pytest.importorskip('cadquery');root=tmp_path_factory.mktemp('native-source')
    r=json.loads(make_fixture(root).read_text());r['board']['step_path']=str((root/r['board']['step_path']).resolve())
    r['board']['metadata']['source_export_mode']='native_identity_bytes'
    rp,result=run(root,r);assert result['status']=='ok',result['issues']
    cache={'result_path':str(rp),'sha256':hashlib.sha256(rp.read_bytes()).hexdigest()}
    return r,rp,result,cache


def test_native_export_preserves_actual_bytes_and_avoids_source_reencoding(native):
    r,rp,result,cache=native
    refs={a['kind']:a for a in result['artifacts']}
    assert (rp.parent/refs['source_step']['path']).read_bytes()==(rp.parent/refs['normalized_input_step']['path']).read_bytes()
    assert result['metadata']['source_export']['policy']=='native_identity_bytes'
    assert 'assembly_manifest' in refs and 'assembly_step' not in refs
    assert not any(c['name']=='normalized_input_step_roundtrip' for c in result['checks'])
    assert result['metadata']['kernel']['ocp_version']


def test_cache_reuses_only_same_source_validity_and_new_geometry_is_checked(native,tmp_path):
    r,_,_,cache=native;r=copy.deepcopy(r);r['job_id']='new-case-cached';r['constraints']['manufacturing']['lid_thickness_mm']=3.5
    _,result=run(tmp_path,r,cache)
    assert result['status']=='ok',result['issues']
    inputs=[c for c in result['checks'] if c['name'].startswith('input_solid_')]
    assert inputs and all(c['reused_check_provenance']['result_sha256']==cache['sha256'] for c in inputs)
    assert result['metadata']['source_validation_cache']['fresh_source_import'] is True
    newcase=next(c for c in result['checks'] if c['name']=='lid_valid_single_solid')
    assert 'reused_check_provenance' not in newcase
    assert result['metadata']['generated_configuration']['lid_thickness_mm']==3.5


def test_source_cache_does_not_bypass_a_new_required_support_conflict(native,tmp_path):
    r,_,_,cache=native;r=copy.deepcopy(r);r['job_id']='cached-new-interference'
    r['constraints']['keepouts'].append({'id':'new-support-conflict','shape':'box','purpose':'no_material',
        'min_mm':[4.,4.,-5.],'max_mm':[6.,6.,1.],'source':'synthetic explicit new keepout crosses existing support'})
    _,result=run(tmp_path,r,cache)
    assert result['status']=='failed'
    assert any(i['code']=='PROTECTED_FEATURE_CONFLICT' for i in result['issues'])
    assert result['metadata']['source_validation_cache']['new_case_checks_reused'] is False


@pytest.mark.parametrize('alter',['result_hash','artifact_bytes','ocp'])
def test_corrupt_cache_never_skips_fresh_source_validation(native,tmp_path,alter):
    r,rp,old,cache=native;r=copy.deepcopy(r);cache=copy.deepcopy(cache)
    if alter=='result_hash':cache['sha256']='0'*64
    elif alter=='ocp':cache['ocp_version']='invalid-version'
    else:
        copied=tmp_path/'prior';shutil.copytree(rp.parent,copied)
        cache['result_path']=str(copied/rp.name)
        a=next(a for a in old['artifacts'] if a['kind']=='normalized_input_step')
        with (copied/a['path']).open('ab') as f:f.write(b'\n')
    _,result=run(tmp_path/'next',r,cache)
    assert result['status']=='needs_input'
    assert not any(c['name'].startswith('input_solid_') for c in result['checks'])
    assert not result['artifacts']
