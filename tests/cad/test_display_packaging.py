"""Declared rear screen prototype: original PCB holes, bridges and real voids."""
import json
from pathlib import Path
import pytest
from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from synthetic_fixture import make_fixture


def request(root):
    r = json.loads(make_fixture(root).read_text())
    r['constraints']['manufacturing'].update(enclosure_fit='compact',clearance_xy_mm=.5,standoff_height_mm=2.)
    r['constraints']['manufacturing']['pcb_mount']['pilot_depth_mm']=1.5
    r['constraints']['ports'][0]['component_solid_indices']=[1]
    r['constraints']['display']={
        'id':'screen','size_mm':[70,50,3.2],'orientation':'pcb_back_parallel',
        'source':'synthetic user-dimension screen box; no manufacturer detail',
        'clearance_xy_mm':.5,'clearance_z_mm':.5,'board_to_display_gap_mm':4.,
        'pcb_bridge_thickness_mm':1.5,'pcb_post_height_mm':2.,
        'face_opening':{'mode':'prototype_edge_ledge','ledge_width_mm':1.},
        'ribbon_channel':{'face':'x_max','center_tangent_mm':20.,'width_mm':20.,'height_mm':2.,
                          'source':'synthetic static channel; bending unverified'}}
    r['constraints']['keepouts']=[]
    return r


def generate_request(root,r):
    p=root/'request.json';p.write_text(json.dumps(r));rp=generate(p,root/'generated')
    return rp,json.loads(rp.read_text())


def artifact(rp,r,kind):
    import cadquery as cq
    row=next(a for a in r['artifacts'] if a['kind']==kind)
    return read_step(cq,rp.parent/row['path'])


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    pytest.importorskip('cadquery')
    root=tmp_path_factory.mktemp('display')
    p,r=generate_request(root,request(root))
    assert r['status']=='ok',r['issues']
    return p,r


def test_real_display_envelope_and_case_are_nonpenetrating_single_solids(generated):
    rp,r=generated
    d=r['metadata']['display']
    assert d['envelope_bounds']['min_mm']==pytest.approx([-5,-5,-7.2])
    assert d['envelope_bounds']['max_mm']==pytest.approx([65,45,-4])
    assert d['manufacturer_component_model'] is False
    assert d['active_area_verified'] is False
    assert r['metadata']['body_outer_bounds']['min_mm']==pytest.approx([-7.5,-7.5,-9.7])
    screen=artifact(rp,r,'screen_envelope_step')
    assert screen.Volume()==pytest.approx(70*50*3.2)
    for kind in ('base_step','lid_step'):
        s=artifact(rp,r,kind)
        assert s.isValid() and len(s.Solids())==1
        assert s.intersect(screen).Volume()==pytest.approx(0,abs=1e-7)
    assert len(artifact(rp,r,'assembly_step').Solids())==5


def test_pcb_posts_are_short_and_bridges_do_not_go_through_screen(generated):
    import cadquery as cq
    rp,r=generated;d=r['metadata']['display'];base=artifact(rp,r,'base_step')
    assert len(d['pcb_bridge_bounds'])==len(d['pcb_post_bounds'])==4
    assert all(row['min_mm'][2]==pytest.approx(-3.5) and row['max_mm'][2]==pytest.approx(-2)
               for row in d['pcb_bridge_bounds'])
    assert all(row['max_mm'][2]==pytest.approx(0) for row in d['pcb_post_bounds'])
    assert base.isInside(cq.Vector(7.1,5,-.5),1e-6)
    assert not base.isInside(cq.Vector(5,5,-.5),1e-6) # real pilot
    assert not base.isInside(cq.Vector(5,5,-5),1e-6) # no pillar through screen
    assert base.isInside(cq.Vector(5,5,-1.8),1e-6) # blind pilot leaves bottom
    assert [h['center_mm'][:2] for h in r['metadata']['mounting_holes']]==[[5,5],[55,5],[5,35],[55,35]]


def test_window_and_transverse_ribbon_channel_are_real_voids(generated):
    import cadquery as cq
    rp,r=generated;d=r['metadata']['display'];base=artifact(rp,r,'base_step')
    assert not base.isInside(cq.Vector(30,20,-9),1e-6) # front window
    assert base.isInside(cq.Vector(-4.5,20,-8),1e-6) # 1mm screen edge support
    assert not base.isInside(cq.Vector(66.5,20,-2.5),1e-6) # channel in side wall
    b=d['ribbon_channel_bounds'];lo,hi=b['min_mm'],b['max_mm']
    probe=cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)),cq.Vector(*lo))
    assert base.intersect(probe).Volume()==pytest.approx(0,abs=1e-7)


def test_short_or_unknown_display_stack_is_rejected_not_filled_in(tmp_path):
    pytest.importorskip('cadquery');r=request(tmp_path)
    r['constraints']['display']['board_to_display_gap_mm']=2.
    _,result=generate_request(tmp_path,r)
    assert result['status']=='needs_input'
    assert 'bridge' in result['issues'][0]['message']
    assert 'kernel' not in result['metadata']


def test_open_operation_area_is_actually_cut_without_moving_supports(tmp_path):
    cq=pytest.importorskip('cadquery');r=request(tmp_path)
    r['constraints']['open_operation_regions']=[{
        'id':'controls-open','shape':'box','min_mm':[18,10,2.0],'max_mm':[45,30,20],
        'source':'synthetic explicit open operating region'}]
    rp,result=generate_request(tmp_path,r)
    assert result['status']=='ok',result['issues']
    probe=cq.Solid.makeBox(27,20,18,cq.Vector(18,10,2))
    for kind in ('base_step','lid_step'):
        s=artifact(rp,result,kind)
        assert s.isValid() and len(s.Solids())==1
        assert s.intersect(probe).Volume()==pytest.approx(0,abs=1e-7)
    assert result['metadata']['open_operation_regions'][0]['id']=='controls-open'


def test_display_proxy_survives_identity_source_recovery(tmp_path,monkeypatch):
    cq=pytest.importorskip('cadquery');r=request(tmp_path);exporter=cq.exporters.export
    def corrupt_optional(shape,filename,*args,**kwargs):
        if Path(filename).name=='normalized_input.step':shape=shape.scale(1.01)
        return exporter(shape,filename,*args,**kwargs)
    monkeypatch.setattr(cq.exporters,'export',corrupt_optional)
    rp,result=generate_request(tmp_path,r)
    assert result['status']=='ok',result['issues']
    assert result['metadata']['recovery']['policy']=='identity_source_preservation'
    assert artifact(rp,result,'screen_envelope_step').Volume()==pytest.approx(70*50*3.2)
    assert result['metadata']['display']['pcb_post_bounds'][0]['max_mm'][2]==pytest.approx(0)
