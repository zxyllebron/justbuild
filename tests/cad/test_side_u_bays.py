"""Lid-mounted local U bays: exact unchanged service void and insertion path."""
import json
from pathlib import Path
import pytest
from justbuild.cad import generate
from justbuild.verification.geometry import linear_brep_sweep
from test_removable_carrier import request as carrier_request, artifact


def request(root):
    r=carrier_request(root)
    r['constraints']['ports'][0]['surface_access']={
        'mode':'side_u_bay','mount_part':'lid','panel_thickness_mm':.8,
        'max_recess_mm':.5,'recess_margin_mm':1.5,'open_bottom_z_mm':1.,
        'sliding_clearance_mm':.3,'source':'explicit synthetic prototype U bay'}
    return r


def run(root,r):
    p=root/'request.json';p.write_text(json.dumps(r));rp=generate(p,root/'generated')
    return rp,json.loads(rp.read_text())


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    pytest.importorskip('cadquery');root=tmp_path_factory.mktemp('side-u-bay')
    rp,result=run(root,request(root));assert result['status']=='ok',result['issues']
    return rp,result


def test_new_lid_has_real_local_lip_original_window_and_retracted_plane(generated):
    import cadquery as cq
    rp,r=generated;base=artifact(rp,r,'base_step');lid=artifact(rp,r,'lid_step')
    assert lid.isValid() and len(lid.Solids())==1
    assert r['metadata']['side_bays'][0]['mount_part']=='lid'
    assert r['metadata']['side_bays'][0]['local_panel_outer_plane_mm']==pytest.approx(64.5)
    assert lid.isInside(cq.Vector(64.1,13.75,4),1e-6) # actual local border
    assert not base.isInside(cq.Vector(64.1,13.75,4),1e-6)
    probe=cq.Solid.makeBox(36,12,7,cq.Vector(64,14,.6))
    assert lid.intersect(probe).Volume()==pytest.approx(0,abs=1e-7)
    assert base.intersect(probe).Volume()==pytest.approx(0,abs=1e-7)
    assert r['metadata']['port_surface_access'][0]['recess_mm']==pytest.approx(.5)


def test_side_bay_additions_preserve_source_lid_clearance_and_all_stages(generated):
    rp,r=generated
    shapes={name:artifact(rp,r,kind) for name,kind in (
        ('base','base_step'),('lid','lid_step'),('pcb_carrier','pcb_carrier_step'),
        ('screen','screen_envelope_step'),('board','normalized_input_step'))}
    assert shapes['lid'].distance(shapes['board'])>=1.-.001
    for moving,against in [('screen',['base']),('pcb_carrier',['base','screen']),
                           ('board',['base','screen','pcb_carrier']),('lid',['base','screen','pcb_carrier','board'])]:
        sweep,evidence=linear_brep_sweep(shapes[moving],[0,0,30])
        for other in against:assert sweep.intersect(shapes[other]).Volume()==pytest.approx(0,abs=1e-7),(moving,other)


def test_u_bay_does_not_accept_missing_explicit_sliding_and_source_requirements(tmp_path):
    pytest.importorskip('cadquery');r=request(tmp_path)
    del r['constraints']['ports'][0]['surface_access']['sliding_clearance_mm']
    _,result=run(tmp_path,r)
    assert result['status']=='needs_input'
    assert 'kernel' not in result['metadata']



def test_shell_only_pocket_preserves_carrier_support_below_original_service(tmp_path):
    import copy
    cq=pytest.importorskip('cadquery');r=carrier_request(tmp_path)
    p=copy.deepcopy(r['constraints']['ports'][0])
    p.update(id='edge-service',center_mm=[64.,35.,3.],width_mm=6.,height_mm=9.)
    p.pop('component_solid_indices',None)
    p['service_envelope']={'min_mm':[64.,32.,-1.5],'max_mm':[100.,38.,7.5],
                           'source':'synthetic unchanged service volume'}
    p['surface_access']={'mode':'side_u_bay','mount_part':'lid','panel_thickness_mm':.8,
        'max_recess_mm':.5,'recess_margin_mm':1.5,'open_bottom_z_mm':0.,
        'sliding_clearance_mm':.3,'source':'synthetic explicit part-scoped U bay'}
    r['constraints']['ports'].append(p)
    rp,result=run(tmp_path,r)
    assert result['status']=='ok',result['issues']
    b=result['metadata']['side_bays'][0]['pocket_bounds'];lo,hi=b['min_mm'],b['max_mm']
    pocket=cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)),cq.Vector(*lo))
    carrier=artifact(rp,result,'pcb_carrier_step')
    # A shell-only low pocket crosses this still intact carrier bridge. Applying
    # a shell cutter to that separate part, or rejecting it, is incorrect.
    assert carrier.intersect(pocket).Volume()>6.
    for kind in ('base_step','lid_step'):
        assert artifact(rp,result,kind).intersect(pocket).Volume()==pytest.approx(0,abs=1e-7)
    service=p['service_envelope'];lo,hi=service['min_mm'],service['max_mm']
    opening=cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)),cq.Vector(*lo))
    assert carrier.intersect(opening).Volume()==pytest.approx(0,abs=1e-7)


def test_overlapping_bays_share_lower_cut_so_return_walls_stay_connected():
    from justbuild.cad.side_bay import side_bay
    cq=pytest.importorskip('cadquery')
    sa={'mode':'side_u_bay','mount_part':'lid','panel_thickness_mm':1.,
        'max_recess_mm':.5,'recess_margin_mm':1.5,'open_bottom_z_mm':0.,
        'sliding_clearance_mm':.3,'source':'synthetic overlapping bays'}
    a={'id':'a','face':'x_min','center_mm':[0.,10.,4.],'width_mm':10.,'height_mm':8.,'surface_access':sa}
    b={'id':'b','face':'x_min','center_mm':[-1.,19.,5.],'width_mm':10.,'height_mm':5.,
       'surface_access':{**sa,'open_bottom_z_mm':3.}}
    m={'wall_thickness_mm':2.,'geometry_tolerance_mm':.001}
    outer={'min_mm':[-7.5,-7.5,-9.7],'max_mm':[67.5,47.5,11.]}
    items=[side_bay(cq,p,m,outer,8.,[a,b]) for p in (a,b)]
    assert {item[3]['pocket_bounds']['min_mm'][2] for item in items}=={-1.5}
    assert all(item[3]['merged_bay_group_ids']==['a','b'] for item in items)
    lid=cq.Solid.makeBox(75,55,3,cq.Vector(-7.5,-7.5,8.))
    for pocket,positive,_,_ in items:lid=lid.cut(pocket).fuse(positive)
    for pocket,_,_,_ in items:lid=lid.cut(pocket)
    assert lid.isValid() and len(lid.clean().Solids())==1


def test_explicit_one_mm_protrusion_uses_real_inward_panel_and_smaller_m2_ears(tmp_path):
    cq=pytest.importorskip('cadquery');r=request(tmp_path)
    m=r['constraints']['manufacturing'];m.update(wall_thickness_mm=1.6,base_thickness_mm=1.6,lid_thickness_mm=2.)
    m['lid_fastener'].update(screw_id='prototype-M2x14-self-tapping',post_diameter_mm=6.2,
        pilot_diameter_mm=1.6,pilot_depth_mm=4.5,clearance_diameter_mm=2.2,
        head_diameter_mm=4.,head_depth_mm=1.2,screw_length_mm=14.,preferred_ear_axis='y')
    r['constraints']['display']['carrier_clamp']='shared_lid_fastener'
    r['constraints']['ports'][0]['surface_access']['min_protrusion_mm']=1.
    r['board']['metadata']['source_export_mode']='native_identity_bytes'
    rp,result=run(tmp_path,r)
    assert result['status']=='ok',result['issues']
    b=result['metadata']['body_outer_bounds']
    assert [b['max_mm'][i]-b['min_mm'][i] for i in (0,1)]==pytest.approx([74.2,54.2])
    entry=result['metadata']['side_bays'][0]
    assert entry['local_panel_outer_plane_mm']==pytest.approx(63.)
    assert entry['signed_protrusion_mm']==pytest.approx(1.)
    assert result['metadata']['enclosure_fit']['local_ear_axis']==1
    assert result['metadata']['hardware']['carrier_clamp']['prototype_screw_length_mm']==14.
    lid=artifact(rp,result,'lid_step');board=artifact(rp,result,'normalized_input_step')
    # Actual annular lip stock at X=63, with the original connector fixed X=64.
    collar=cq.Solid.makeBox(20,.5,5,cq.Vector(60.,13.5,2.))
    lip=lid.intersect(collar)
    assert lip.Volume()>0 and lip.BoundingBox().xmax==pytest.approx(63.)
    assert lid.distance(board)>=1.-.001


@pytest.mark.parametrize('value',[-1.,True])
def test_invalid_minimum_protrusion_is_rejected_before_kernel(tmp_path,value):
    pytest.importorskip('cadquery');r=request(tmp_path)
    r['constraints']['ports'][0]['surface_access']['min_protrusion_mm']=value
    _,result=run(tmp_path,r)
    assert result['status']=='needs_input' and 'kernel' not in result['metadata']
