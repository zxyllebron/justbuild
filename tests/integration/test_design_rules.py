from copy import deepcopy
from pathlib import Path

import pytest

from justbuild.contracts import digest,write_json
from justbuild.design_rules import apply_access_targets,default_fit_policy,fit_policy_checks
from justbuild.inspection import default_manufacturing


def test_new_defaults_are_compact_but_explicit_legacy_requests_stay_untouched():
    m=default_manufacturing()
    assert m['enclosure_fit']=='compact'
    assert m['clearance_xy_mm']==.5
    assert m['fit_policy']['maximum_ordinary_side_gap_mm']==1
    old={'constraints':{'manufacturing':{'clearance_xy_mm':1},'ports':[]}}
    assert apply_access_targets(old)==old
    assert fit_policy_checks(old,{},Path('/unused'))==[]


def test_named_buttons_receive_exposure_targets_and_internal_connectors_stay_internal():
    r={'constraints':{'manufacturing':default_manufacturing(),'functional_features':[
        {'kind':'FEL tactile switch','port_ids':['key']}], 'ports':[
        {'id':'key','access':'maintenance'}, {'id':'usb','access':'external'},
        {'id':'ribbon','access':'internal'}]}}
    result=apply_access_targets(r)
    assert result['constraints']['ports'][0]['surface_access']['max_recess_mm']==0
    assert result['constraints']['ports'][1]['surface_access']['max_recess_mm']==.5
    assert 'surface_access' not in result['constraints']['ports'][2]
    assert 'surface_access' not in r['constraints']['ports'][0]


def fixture(tmp_path, side_gap=.5, front=-2.5):
    cq=pytest.importorskip('cadquery')
    def box(lo,hi):return cq.Solid.makeBox(*[b-a for a,b in zip(lo,hi)],cq.Vector(*lo))
    pcb=box([0,0,0],[20,14,1]);connector=box([front,6,1.2],[1,8,2.2])
    wall=2
    outerlo=[-side_gap-wall,-side_gap-wall,-2]
    outerhi=[20+side_gap+wall,14+side_gap+wall,5]
    base=box(outerlo,outerhi).cut(box([-side_gap,-side_gap,0],[20+side_gap,14+side_gap,6]))
    base=base.cut(box([outerlo[0]-1,5,.7],[front+.01,9,2.7]))
    lid=box([outerlo[0],outerlo[1],5],[outerhi[0],outerhi[1],7])
    artifacts=[]
    for kind,shape in [('normalized_input_step',cq.Compound.makeCompound([pcb,connector])),('base_step',base),('lid_step',lid)]:
        p=tmp_path/(kind+'.step');cq.exporters.export(shape,str(p));artifacts.append({'kind':kind,'path':str(p),'sha256':digest(p)})
    r={'board':{'metadata':{'pcb_solid_index':0}},'constraints':{'manufacturing':{'enclosure_fit':'compact','fit_policy':default_fit_policy(),'geometry_tolerance_mm':.001},
        'ports':[{'id':'usb','access':'external','face':'x_min','shape':'rectangle','center_mm':[front,7,1.7],
                  'width_mm':4,'height_mm':2,'component_solid_indices':[1]}]}}
    cad={'artifacts':artifacts};p=write_json(tmp_path/'cad_result.json',cad)
    return r,cad,p


def test_real_exported_brep_detects_old_full_perimeter_padding(tmp_path):
    r,cad,p=fixture(tmp_path,side_gap=9)
    checks=fit_policy_checks(r,cad,p)
    gaps=[x for x in checks if x['id'].startswith('compact_side_gap')]
    assert len(gaps)==4 and all(x['status']=='fail' for x in gaps)
    assert all(x['measurements']['side_gap_mm']==pytest.approx(9) for x in gaps)
    assert next(x for x in checks if x['id']=='surface_access_usb')['status']=='fail'


def test_real_exported_brep_distinguishes_flush_connector_from_deep_hole(tmp_path):
    flush=tmp_path/'flush';flush.mkdir();r,cad,p=fixture(flush)
    checks=fit_policy_checks(r,cad,p)
    assert checks and all(x['status']=='pass' for x in checks),checks
    deep=tmp_path/'deep';deep.mkdir();r,cad,p=fixture(deep,front=-1)
    check=next(x for x in fit_policy_checks(r,cad,p) if x['id']=='surface_access_usb')
    assert check['status']=='fail'
    assert check['measurements']['signed_protrusion_mm']==pytest.approx(-1.5)


def test_missing_component_datum_is_required_unperformed_not_a_pass(tmp_path):
    r,cad,p=fixture(tmp_path);r['constraints']['ports'][0].pop('component_solid_indices')
    check=next(x for x in fit_policy_checks(r,cad,p) if x['id']=='surface_access_usb')
    assert check['status']=='not_checked' and check['required'] is True


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,-1])
def test_invalid_maximum_cannot_disable_the_fit_policy(bad):
    policy=default_fit_policy();policy['maximum_ordinary_side_gap_mm']=bad
    checks=fit_policy_checks({'constraints':{'manufacturing':{'fit_policy':policy}}},{},Path('/unused'))
    assert len(checks)==1 and checks[0]['status']=='fail'


def test_user_sized_display_space_is_bounded_and_not_unexplained_padding(tmp_path):
    fit=tmp_path/'fit';fit.mkdir();r,cad,p=fixture(fit,side_gap=5.5,front=-7.5)
    r['constraints']['display']={'orientation':'pcb_back_parallel','size_mm':[30,24,3.2],'clearance_xy_mm':.5}
    gaps=[x for x in fit_policy_checks(r,cad,p) if x['id'].startswith('compact_side_gap')]
    assert all(x['status']=='pass' for x in gaps)
    assert all(x['measurements']['justified_accessory_space'] for x in gaps)
    excessive=tmp_path/'extra';excessive.mkdir();r,cad,p=fixture(excessive,side_gap=9)
    r['constraints']['display']={'orientation':'pcb_back_parallel','size_mm':[30,24,3.2],'clearance_xy_mm':.5}
    gaps=[x for x in fit_policy_checks(r,cad,p) if x['id'].startswith('compact_side_gap')]
    assert all(x['status']=='fail' for x in gaps)


def test_declaring_open_region_cannot_exempt_a_deep_external_connector(tmp_path):
    r,cad,p=fixture(tmp_path,front=-1)
    r['constraints']['open_operation_regions']=[{'id':'fake-shortcut','shape':'box','source':'test',
        'min_mm':[-3,5,.7],'max_mm':[-1,9,2.7]}]
    check=next(x for x in fit_policy_checks(r,cad,p) if x['id']=='surface_access_usb')
    assert check['status']=='fail'


def test_reviewed_single_solid_reference_is_carried_into_compact_size_budget():
    r={'constraints':{'manufacturing':default_manufacturing(),'ports':[
        {'id':'usb','access':'external','solid_index':7}]}}
    result=apply_access_targets(r)
    assert result['constraints']['ports'][0]['component_solid_indices']==[7]
    assert 'component_solid_indices' not in r['constraints']['ports'][0]


def test_source_identified_sound_vent_does_not_receive_a_plug_mating_target():
    r={'constraints':{'manufacturing':default_manufacturing(),'functional_features':[
        {'kind':'SMD buzzer','port_ids':['sound']},
        {'kind':'2-contact speaker connector','port_ids':['spk']}], 'ports':[
        {'id':'sound','access':'external','diameter_mm':3},
        {'id':'spk','access':'external','width_mm':8}]}}
    result=apply_access_targets(r)
    sound,spk=result['constraints']['ports']
    assert sound['fit_requirement']=='acoustic_vent'
    assert sound['diameter_mm']==3 and 'surface_access' not in sound
    assert spk['fit_requirement']=='near_flush_connector'
    assert spk['surface_access']['max_recess_mm']==.5
    assert 'fit_requirement' not in r['constraints']['ports'][0]


def test_open_bottom_bay_measures_actual_lips_but_never_accepts_blocked_approach(tmp_path):
    cq=pytest.importorskip('cadquery')
    r,cad,p=fixture(tmp_path,side_gap=9,front=-1)
    def box(lo,hi):return cq.Solid.makeBox(*[b-a for a,b in zip(lo,hi)],cq.Vector(*lo))
    artifacts={a['kind']:a for a in cad['artifacts']}
    base_path=Path(artifacts['base_step']['path'])
    base=cq.importers.importStep(str(base_path)).val()
    cq.exporters.export(base.cut(box([-12,4,1.8],[0,10,5.1])),str(base_path))
    artifacts['base_step']['sha256']=digest(base_path)
    lid_path=Path(artifacts['lid_step']['path'])
    lid=cq.importers.importStep(str(lid_path)).val()
    local_panel=box([-1.5,4.5,1.8],[-.5,9.5,6]).cut(box([-2,5,.7],[0,9,2.7]))
    cq.exporters.export(lid.fuse(local_panel),str(lid_path))
    artifacts['lid_step']['sha256']=digest(lid_path)
    legacy=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert legacy['status']=='fail'  # The distant lower wall is a closed-hole lip only.
    r['constraints']['ports'][0]['surface_access']={'mode':'side_u_bay','open_bottom_z_mm':1.8}
    result=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert result['status']=='pass',result
    assert result['measurements']['signed_protrusion_mm']==pytest.approx(-.5)
    assert result['measurements']['full_unchanged_opening_clear'] is True
    base_path=Path(artifacts['base_step']['path'])
    base=cq.importers.importStep(str(base_path)).val()
    cq.exporters.export(cq.Compound.makeCompound([base,box([-4,6,1.1],[-3,8,2.3])]),str(base_path))
    artifacts['base_step']['sha256']=digest(base_path)
    blocked=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert blocked['status']=='fail'
    assert blocked['measurements']['shell_material_volume_mm3']>0


def test_positive_button_target_rejects_flush_and_measures_real_protruding_datum(tmp_path):
    cq=pytest.importorskip('cadquery')
    for name,front,expected in [('flush',-2.5,'fail'),('outward',-3.5,'pass')]:
        folder=tmp_path/name;folder.mkdir()
        r,cad,p=fixture(folder,front=front)
        port=r['constraints']['ports'][0]
        port.update(component_kind='button',fit_requirement='exposed_button',
                    surface_access={'min_protrusion_mm':1.0})
        checks=fit_policy_checks(r,cad,p)
        c=next(c for c in checks if c['id']=='surface_access_usb')
        assert c['status']==expected,c
        assert c['measurements']['minimum_protrusion_mm']==1
        assert c['measurements']['signed_protrusion_mm']==pytest.approx(0 if name=='flush' else 1)


@pytest.mark.parametrize('target',[-.1,float('nan'),float('inf'),True])
def test_invalid_per_button_protrusion_target_fails_closed(tmp_path,target):
    r,cad,p=fixture(tmp_path)
    r['constraints']['ports'][0].update(component_kind='button',fit_requirement='exposed_button',
        surface_access={'min_protrusion_mm':target})
    c=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert c['status']=='fail'


def test_projecting_button_inside_overall_outline_requires_real_nearby_panel(tmp_path):
    cq=pytest.importorskip('cadquery')
    r,cad,p=fixture(tmp_path,side_gap=9,front=-2.5)
    def box(lo,hi):return cq.Solid.makeBox(*[b-a for a,b in zip(lo,hi)],cq.Vector(*lo))
    artifacts={a['kind']:a for a in cad['artifacts']}
    bp=Path(artifacts['base_step']['path']);base=cq.importers.importStep(str(bp)).val()
    cq.exporters.export(base.cut(box([-12,4,1.8],[0,10,5.1])),str(bp));artifacts['base_step']['sha256']=digest(bp)
    lp=Path(artifacts['lid_step']['path']);lid=cq.importers.importStep(str(lp)).val()
    panel=box([-1.5,4.5,1.8],[-.5,9.5,6]).cut(box([-2,5,.7],[0,9,2.7]))
    cq.exporters.export(lid.fuse(panel),str(lp));artifacts['lid_step']['sha256']=digest(lp)
    r['constraints']['ports'][0].update(component_kind='button',fit_requirement='exposed_button',
        surface_access={'mode':'side_u_bay','open_bottom_z_mm':1.8,'min_protrusion_mm':1.,'panel_thickness_mm':1.})
    c=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert c['status']=='pass',c
    assert c['measurements']['signed_protrusion_mm']==pytest.approx(1.)
    # Absence of the nearby panel must not use the distant opposite shell wall.
    cq.exporters.export(lid,str(lp));artifacts['lid_step']['sha256']=digest(lp)
    c=next(c for c in fit_policy_checks(r,cad,p) if c['id']=='surface_access_usb')
    assert c['status']=='not_checked',c
