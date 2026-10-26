"""A removable carrier permits screen-first assembly and clamps on shared M3 axes."""
import json
from pathlib import Path
import pytest
from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from justbuild.verification.geometry import linear_brep_sweep
from test_display_packaging import request as display_request


def request(root):
    r=display_request(root)
    r['constraints']['display'].update(support_structure='removable_pcb_carrier',carrier_clearance_xy_mm=.3,
                                     carrier_slot_clearance_mm=.3,carrier_frame_width_mm=2.,carrier_clamp='shared_lid_m3')
    return r


def artifact(path,result,kind):
    import cadquery as cq
    row=next(a for a in result['artifacts'] if a['kind']==kind)
    return read_step(cq,path.parent/row['path'])


def run(root,r):
    p=root/'request.json';p.write_text(json.dumps(r));rp=generate(p,root/'generated')
    return rp,json.loads(rp.read_text())


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    pytest.importorskip('cadquery');root=tmp_path_factory.mktemp('carrier')
    rp,r=run(root,request(root));assert r['status']=='ok',r['issues']
    return rp,r


def test_carrier_is_real_single_solid_with_original_posts_and_clearance_clamp(generated):
    import cadquery as cq
    rp,r=generated
    carrier=artifact(rp,r,'pcb_carrier_step');base=artifact(rp,r,'base_step');lid=artifact(rp,r,'lid_step')
    assert carrier.isValid() and len(carrier.Solids())==1
    assert r['metadata']['hardware']['pcb_mount_part']=='pcb_carrier'
    for s in (base,lid):assert carrier.intersect(s).Volume()==pytest.approx(0,abs=1e-7)
    # Existing PCB post and blind pilot now belong only to the removable piece.
    assert carrier.isInside(cq.Vector(7.1,5,-.5),1e-6)
    assert not base.isInside(cq.Vector(7.1,5,-.5),1e-6)
    assert not carrier.isInside(cq.Vector(5,5,-.5),1e-6)
    assert carrier.isInside(cq.Vector(5,5,-1.8),1e-6)
    for x,y,_ in r['metadata']['lid_screw_centers_mm']:
        assert not carrier.isInside(cq.Vector(x,y,-2.5),1e-6)
        assert carrier.isInside(cq.Vector(x+2.1,y,-2.5),1e-6)
        assert not base.isInside(cq.Vector(x+2.1,y,-2.5),1e-6)
        assert lid.isInside(cq.Vector(x+2.1,y,0),1e-6)
        assert not lid.isInside(cq.Vector(x,y,0),1e-6)
    assert len(artifact(rp,r,'assembly_step').Solids())==6


def test_full_screen_carrier_board_and_lid_axial_assembly_sweeps_have_no_penetration(generated):
    rp,r=generated
    shapes={name:artifact(rp,r,kind) for name,kind in (
        ('base','base_step'),('lid','lid_step'),('pcb_carrier','pcb_carrier_step'),
        ('screen','screen_envelope_step'),('board','normalized_input_step'))}
    sequence=[('screen',['base']),('pcb_carrier',['base','screen']),
              ('board',['base','screen','pcb_carrier']),('lid',['base','screen','pcb_carrier','board'])]
    for moving,obstacles in sequence:
        sweep,evidence=linear_brep_sweep(shapes[moving],[0,0,30])
        for other in obstacles:
            assert sweep.intersect(shapes[other]).Volume()==pytest.approx(0,abs=1e-7),(moving,other)


def test_ribbon_void_passes_through_carrier_without_disconnect(generated):
    import cadquery as cq
    rp,r=generated;b=r['metadata']['display']['ribbon_channel_bounds'];lo,hi=b['min_mm'],b['max_mm']
    probe=cq.Solid.makeBox(*(hi[i]-lo[i] for i in range(3)),cq.Vector(*lo))
    for kind in ('base_step','lid_step','pcb_carrier_step'):
        s=artifact(rp,r,kind)
        assert s.isValid() and len(s.Solids())==1
        assert s.intersect(probe).Volume()==pytest.approx(0,abs=1e-7)


def test_carrier_and_proxy_survive_native_source_preservation(tmp_path,monkeypatch):
    cq=pytest.importorskip('cadquery');r=request(tmp_path);exporter=cq.exporters.export
    def corrupt(shape,filename,*args,**kwargs):
        if Path(filename).name=='normalized_input.step':shape=shape.scale(1.01)
        return exporter(shape,filename,*args,**kwargs)
    monkeypatch.setattr(cq.exporters,'export',corrupt)
    rp,result=run(tmp_path,r)
    assert result['status']=='ok',result['issues']
    assert result['metadata']['recovery']['policy']=='identity_source_preservation'
    assert len(artifact(rp,result,'pcb_carrier_step').Solids())==1
    assert any(a['kind']=='pcb_carrier_stl' for a in result['artifacts'])



def test_noncontact_sliding_interfaces_have_real_gap_not_tolerance_touch(generated):
    import cadquery as cq
    rp,r=generated
    base=artifact(rp,r,'base_step');lid=artifact(rp,r,'lid_step')
    carrier=artifact(rp,r,'pcb_carrier_step')
    bottom=r['metadata']['display']['carrier_bounds']['min_mm'][2]
    rim=r['metadata']['cavity_bounds']['max_mm'][2]
    # Remove only a .01mm slice at each declared intentional horizontal
    # contact. Every remaining sliding face must have an actual gap.
    above_seat=cq.Solid.makeBox(400,400,100,cq.Vector(-150,-150,bottom+.01))
    below_rim=cq.Solid.makeBox(400,400,rim-.01+100,cq.Vector(-150,-150,-100))
    assert carrier.intersect(above_seat).distance(base)>.005
    assert lid.distance(base.intersect(below_rim))>.005
