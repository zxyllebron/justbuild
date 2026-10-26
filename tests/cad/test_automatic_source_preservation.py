"""Real CAD pipeline with an intentionally damaged optional STEP export."""
import json
from pathlib import Path

import pytest

from justbuild.cad import generate
from justbuild.cad.kernel import read_step
from synthetic_fixture import make_fixture


@pytest.mark.parametrize('source_mode', ['identity_mm', 'translated_mm', 'identity_meter'])
def test_only_identity_native_mm_automatically_recovers_an_actual_optional_export_fault(tmp_path, monkeypatch, source_mode):
    import cadquery as cq
    request_path = make_fixture(tmp_path)
    request = json.loads(request_path.read_text())
    source = tmp_path / 'synthetic_board.step'
    if source_mode == 'translated_mm':
        # World +10 mm with an equivalent declared board frame; mechanical
        # constraints stay valid, but copying raw source would be wrong.
        shape = read_step(cq, source).translate((10, 0, 0))
        cq.exporters.export(shape, str(source))
        request['constraints']['board_frame']['origin_mm'] = [10, 0, 0]
    elif source_mode == 'identity_meter':
        shape = read_step(cq, source).scale(.001)
        cq.exporters.export(shape, str(source))
        source.write_text(source.read_text().replace('SI_UNIT(.MILLI.,.METRE.)', 'SI_UNIT($,.METRE.)'))
    request_path.write_text(json.dumps(request))
    original_source = source.read_bytes()
    exporter = cq.exporters.export
    faults = []
    def corrupt_optional_export(shape, filename, *args, **kwargs):
        if Path(filename).name == 'normalized_input.step':
            faults.append(str(filename))
            # Write real, valid, incorrectly scaled STEP geometry. The ordinary
            # BRep roundtrip check must detect the changed mass, not a mocked
            # check result or fabricated numeric measurement.
            shape = shape.scale(1.01)
        return exporter(shape, filename, *args, **kwargs)
    monkeypatch.setattr(cq.exporters, 'export', corrupt_optional_export)
    output = tmp_path / 'output'
    path = generate(request_path, output)
    result = json.loads(path.read_text())
    assert len(faults) == 1
    records = list(output.glob('cad-*/cad_result.json'))
    failures = [p for p in records if json.loads(p.read_text())['status'] == 'failed']
    assert len(failures) == 1
    failed = json.loads(failures[0].read_text())
    failed_checks = [c for c in failed['checks'] if c['status'] == 'fail']
    assert len(failed_checks) == 1 and failed_checks[0]['name'] == 'normalized_input_step_roundtrip'
    assert failed_checks[0]['measurements']['volume_delta_mm3'] > .1
    if source_mode == 'identity_mm':
        assert result['status'] == 'ok' and path != failures[0]
        assert len(records) == 2
        assert (path.parent / 'normalized_input.step').read_bytes() == original_source
        assert result['metadata']['recovery']['policy'] == 'identity_source_preservation'
        assert 'assembly_step' not in {a['kind'] for a in result['artifacts']}
    else:
        assert result['status'] == 'failed' and path == failures[0]
        assert len(records) == 1
        assert json.loads((path.parent / 'progress.json').read_text())['recovery_not_applied'] is True


def test_real_protected_feature_failure_never_triggers_source_recovery(tmp_path):
    request_path = make_fixture(tmp_path)
    request = json.loads(request_path.read_text())
    request['constraints']['keepouts'] = [{
        'id': 'forbidden_support_cut', 'shape': 'box', 'purpose': 'no_material',
        'min_mm': [4, 4, -3], 'max_mm': [6, 6, -1], 'source': 'explicit negative fixture'}]
    request_path.write_text(json.dumps(request))
    path = generate(request_path, tmp_path / 'output')
    result = json.loads(path.read_text())
    assert result['status'] == 'failed'
    assert result['issues'][0]['code'] == 'PROTECTED_FEATURE_CONFLICT'
    assert len(list((tmp_path / 'output').glob('cad-*/cad_result.json'))) == 1
