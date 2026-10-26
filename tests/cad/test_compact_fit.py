"""Real BRep regressions for compact bodies, local screw ears and access wells."""
import json
import struct
from pathlib import Path

import pytest

from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from synthetic_fixture import make_fixture


def run(root, request):
    path = root / 'request.json'
    path.write_text(json.dumps(request))
    result_path = generate(path, root / 'generated')
    return result_path, json.loads(result_path.read_text())


def part(path, result, kind):
    import cadquery as cq
    row = next(a for a in result['artifacts'] if a['kind'] == kind)
    return read_step(cq, path.parent / row['path'])


@pytest.fixture(scope='module')
def compact_fixture(tmp_path_factory):
    pytest.importorskip('cadquery')
    root = tmp_path_factory.mktemp('compact')
    r = json.loads(make_fixture(root).read_text())
    r['constraints']['manufacturing'].update(enclosure_fit='compact', clearance_xy_mm=.5)
    r['constraints']['ports'][0]['component_solid_indices'] = [1]
    path, result = run(root, r)
    assert result['status'] == 'ok', result['issues']
    return path, result


def test_compact_reduces_body_without_moving_original_pcb_holes(compact_fixture):
    path, result = compact_fixture
    m = result['metadata']
    body = m['body_outer_bounds']
    assert [body['max_mm'][i]-body['min_mm'][i] for i in (0, 1)] == pytest.approx([65, 45])
    assert m['enclosure_fit']['whole_side_fastener_reserve_mm'] == 0
    assert m['actual_footprint_mm'][0]*m['actual_footprint_mm'][1] < 88*64
    assert len(m['lid_screw_centers_mm']) == 4
    assert len(m['mounting_holes']) == 4
    assert [h['center_mm'][:2] for h in m['mounting_holes']] == [[5, 5], [55, 5], [5, 35], [55, 35]]
    for name in ('base', 'lid'):
        shape = part(path, result, f'{name}_step')
        assert shape.isValid() and len(shape.Solids()) == 1
    base = part(path, result, 'base_step')
    pcb = part(path, result, 'normalized_input_step').Solids()[0]
    assert base.intersect(pcb).Volume() == pytest.approx(0, abs=1e-7)
    assert {c['status'] for c in result['checks']} <= {'pass', 'not_checked'}


def test_protruding_connector_still_has_actual_wall_cut_and_all_ear_material(compact_fixture):
    import cadquery as cq
    path, result = compact_fixture
    base = part(path, result, 'base_step')
    # Mating face at X=64 already lies outside compact body X=62.5.
    # Cutting only from the mating face outward would leave the body sealed.
    assert not base.isInside(cq.Vector(61.5, 20, 4), 1e-6)
    assert any('synthetic_connector' == p['feature_id'] and p['signed_protrusion_mm'] == pytest.approx(1.5)
               for p in result['metadata']['port_surface_access'])
    for x, y, z in result['metadata']['lid_screw_centers_mm']:
        assert base.isInside(cq.Vector(x+2.1, y, z-1), 1e-6)
        assert not base.isInside(cq.Vector(x, y, z-1), 1e-6)


def test_compact_print_meshes_sit_on_bed_and_lid_has_explicit_flip(compact_fixture):
    path, result = compact_fixture
    for kind in ('base_stl', 'lid_stl'):
        row = next(a for a in result['artifacts'] if a['kind'] == kind)
        data = (path.parent / row['path']).read_bytes()
        count = struct.unpack_from('<I', data, 80)[0]
        vertices = [struct.unpack_from('<3f', data, 84+50*i+12+12*j)
                    for i in range(count) for j in range(3)]
        assert [min(v[i] for v in vertices) for i in (0, 1)] == pytest.approx([0, 0], abs=.05)
        assert min(v[2] for v in vertices) == pytest.approx(0, abs=1e-5)
    assert result['metadata']['stl_placements']['lid']['rotation'] == '180deg_about_x'


def test_optional_recess_is_real_connected_material_at_button_plane(tmp_path):
    cq = pytest.importorskip('cadquery')
    r = json.loads(make_fixture(tmp_path).read_text())
    pcb = read_step(cq, tmp_path / 'synthetic_board.step').Solids()[0]
    button = cq.Solid.makeBox(4, 4, 2, cq.Vector(20, 20, 1.6))
    tall = cq.Solid.makeBox(6, 5, 7, cq.Vector(40, 25, 1.6))
    cq.exporters.export(cq.Compound.makeCompound([pcb, button, tall]), str(tmp_path / 'synthetic_board.step'))
    r['constraints']['manufacturing'].update(enclosure_fit='compact', clearance_xy_mm=.5)
    r['constraints']['ports'] = [{
        'id': 'button', 'source': 'synthetic button', 'access': 'external', 'shape': 'rectangle',
        'face': 'z_max', 'center_mm': [22, 22, 3.6], 'width_mm': 6, 'height_mm': 6,
        'insertion_direction': [0, 0, -1], 'component_solid_indices': [1],
        'surface_access': {'max_recess_mm': 0.0, 'recess_margin_mm': 2.0, 'panel_thickness_mm': 1.0},
        'service_envelope': {'min_mm': [19, 19, 3.6], 'max_mm': [25, 25, 30], 'source': 'synthetic static approach'}}]
    r['constraints']['keepouts'] = []
    path, result = run(tmp_path, r)
    assert result['status'] == 'ok', result['issues']
    lid = part(path, result, 'lid_step')
    assert lid.isValid() and len(lid.Solids()) == 1
    assert lid.isInside(cq.Vector(18, 22, 3.1), 1e-6) # lowered annular panel
    assert not lid.isInside(cq.Vector(18, 22, 5), 1e-6) # recess actually empty
    assert lid.isInside(cq.Vector(16, 22, 5), 1e-6) # actual connecting wall
    assert not lid.isInside(cq.Vector(22, 22, 3.1), 1e-6) # preserved button opening
    assert result['metadata']['port_surface_access'][0]['local_recessed_panel'] is True
    assert result['metadata']['port_surface_access'][0]['recess_mm'] == pytest.approx(0)
    for p in ('base_step', 'lid_step'):
        s = part(path, result, p)
        assert s.intersect(part(path, result, 'normalized_input_step')).Volume() == pytest.approx(0, abs=1e-7)


def test_unrecognized_fit_mode_rejected_before_geometry(tmp_path):
    r = json.loads(make_fixture(tmp_path).read_text())
    r['constraints']['manufacturing']['enclosure_fit'] = 'guess'
    _, result = run(tmp_path, r)
    assert result['status'] == 'unsupported'
    assert result['issues'][0]['code'] == 'UNSUPPORTED_ENCLOSURE_FIT'
    assert 'kernel' not in result['metadata']


def test_compact_source_preservation_retains_actual_ear_bounds_and_lid_placement(tmp_path, monkeypatch):
    cq = pytest.importorskip('cadquery')
    r = json.loads(make_fixture(tmp_path).read_text())
    r['constraints']['manufacturing'].update(enclosure_fit='compact', clearance_xy_mm=.5)
    r['constraints']['ports'][0]['component_solid_indices'] = [1]
    exporter = cq.exporters.export
    def corrupt_only_optional_source(shape, filename, *args, **kwargs):
        if Path(filename).name == 'normalized_input.step':
            shape = shape.scale(1.01)
        return exporter(shape, filename, *args, **kwargs)
    monkeypatch.setattr(cq.exporters, 'export', corrupt_only_optional_source)
    path, result = run(tmp_path, r)
    assert result['status'] == 'ok', result['issues']
    assert result['metadata']['recovery']['policy'] == 'identity_source_preservation'
    assert result['metadata']['enclosure_fit']['mode'] == 'compact'
    assert result['metadata']['actual_footprint_mm'] == pytest.approx([79, 45])
    assert result['metadata']['stl_placements']['lid']['rotation'] == '180deg_about_x'
    assert (path.parent / 'normalized_input.step').read_bytes() == (tmp_path / 'synthetic_board.step').read_bytes()


def test_dimension_budget_is_explicit_about_exposed_direction_and_unknown_geometry():
    from justbuild.cad.compact import body_and_footprint_budget
    r = json.loads(Path(__file__).with_name('example_request.json').read_text())
    m = r['constraints']['manufacturing']
    m.update(enclosure_fit='compact', clearance_xy_mm=.5)
    ports = [{**r['constraints']['ports'][0], 'component_solid_indices': [1]}]
    bounds = [dict(min_mm=[0, 0, 0], max_mm=[60, 40, 1.6]),
              dict(min_mm=[54, 15, 1.6], max_mm=[64, 25, 6.6]),
              dict(min_mm=[20, 15, 1.6], max_mm=[25, 45, 9])]
    budget = body_and_footprint_budget(bounds, 0, ports, m)
    assert budget['fitted_component_bounds']['max_mm'] == pytest.approx([60, 45, 9])
    assert budget['body_outer_bounds']['max_mm'][:2] == pytest.approx([62.5, 47.5])
    assert budget['lid_mating_plane_z_mm'] == pytest.approx(11)
    upper = budget['footprint_upper_bound']
    assert upper['min_mm'][:2] == pytest.approx([-9.5, -9.5])
    assert upper['max_mm'][:2] == pytest.approx([69.5, 54.5])
