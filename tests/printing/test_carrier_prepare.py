"""Three-part preparation contracts only; no printer or actual CLI is accessed."""
from pathlib import Path
import json
import subprocess
import zipfile

import pytest

from justbuild.printing import BambuCliSlicer, FakeSlicer, SliceOutput, prepare_print
from justbuild.printing.common import artifact, read_json, sha256, write_json


def _add_carrier(f):
    request = read_json(f['request'])
    request['constraints']['display'] = {'support_structure': 'removable_pcb_carrier'}
    write_json(f['request'], request)
    cad = read_json(f['cad'])
    for kind, suffix in [('pcb_carrier_step', 'step'), ('pcb_carrier_stl', 'stl')]:
        path = f['root'] / ('pcb_carrier.' + suffix)
        path.write_text('Synthetic carrier contract fixture; not manufactured\n')
        cad['artifacts'].append(artifact(kind, path, units='mm'))
    write_json(f['cad'], cad)
    f['rebind']()


def _prepare(f, slicer):
    return read_json(prepare_print(f['request'], f['cad'], f['verification'],
                                  f['root'] / 'prepared', slicer=slicer))


class TrackingFakeSlicer(FakeSlicer):
    def __init__(self): self.models = None
    def slice(self, models, profiles, settings, output_dir):
        self.models = models
        return super().slice(models, profiles, settings, output_dir)


def test_carrier_has_third_hash_bound_snapshot_and_simulation_stays_simulation(fixture_job):
    f = fixture_job
    _add_carrier(f)
    slicer = TrackingFakeSlicer()
    result = _prepare(f, slicer)
    assert result['status'] == 'ok' and result['simulation'] is True
    assert result['sliced_artifact']['kind'] == 'simulation_slice'
    expected = ['base_stl', 'lid_stl', 'pcb_carrier_stl']
    assert [m.name for m in slicer.models] == [kind + '.stl' for kind in expected]
    assert [ref['kind'] for ref in result['inputs']['models']] == expected
    assert [ref['sha256'] for ref in result['inputs']['models']] == [sha256(path) for path in slicer.models]
    carrier = result['inputs']['models'][-1]
    assert carrier['sha256'] == sha256(f['root'] / 'pcb_carrier.stl')
    assert Path(carrier['path']) != f['root'] / 'pcb_carrier.stl'
    assert read_json(Path(result['sliced_artifact']['path']))['models'][-1] == carrier['sha256']


def test_historical_two_part_input_stays_two_parts(fixture_job):
    slicer = TrackingFakeSlicer()
    result = _prepare(fixture_job, slicer)
    assert result['status'] == 'ok'
    assert [m.name for m in slicer.models] == ['base_stl.stl', 'lid_stl.stl']
    assert [ref['kind'] for ref in result['inputs']['models']] == ['base_stl', 'lid_stl']


@pytest.mark.parametrize('kind', ['pcb_carrier_step', 'pcb_carrier_stl'])
def test_missing_carrier_artifact_cannot_invoke_slicer(fixture_job, kind):
    f = fixture_job
    _add_carrier(f)
    cad = read_json(f['cad'])
    cad['artifacts'] = [ref for ref in cad['artifacts'] if ref['kind'] != kind]
    write_json(f['cad'], cad)
    f['rebind']()
    slicer = TrackingFakeSlicer()
    result = _prepare(f, slicer)
    assert result['status'] == 'needs_input'
    assert result['issues'][-1]['code'] == 'MESH_REQUIRED'
    assert slicer.models is None


@pytest.mark.parametrize('corruption,expected_code', [
    ('hash', 'HASH_MISMATCH'), ('invalid_hash', 'INVALID_HASH'), ('absent', 'MISSING_ARTIFACT'),
    ('directory', 'MISSING_ARTIFACT'), ('empty_path', 'MISSING_PATH'),
    ('nonstring_path', 'MISSING_PATH'), ('nul_path', 'INVALID_PRINT_INPUT'), ('units', 'UNITS_REQUIRED'),
])
def test_bad_carrier_reference_cannot_invoke_slicer(fixture_job, corruption, expected_code):
    f = fixture_job
    _add_carrier(f)
    cad = read_json(f['cad'])
    ref = next(ref for ref in cad['artifacts'] if ref['kind'] == 'pcb_carrier_stl')
    if corruption == 'hash': ref['sha256'] = '0' * 64
    elif corruption == 'invalid_hash': ref['sha256'] = 'not-a-hash'
    elif corruption == 'absent': ref['path'] = str(f['root'] / 'absent.stl')
    elif corruption == 'directory': ref['path'] = str(f['root'])
    elif corruption == 'empty_path': ref['path'] = ''
    elif corruption == 'nonstring_path': ref['path'] = []
    elif corruption == 'nul_path': ref['path'] = 'invalid\x00path'
    else: ref['units'] = 'inch'
    write_json(f['cad'], cad)
    f['rebind']()
    slicer = TrackingFakeSlicer()
    result = _prepare(f, slicer)
    assert result['status'] != 'ok' and result['issues'][-1]['code'] == expected_code
    assert slicer.models is None


def test_simulated_carrier_geometry_cannot_be_relabelled_real(fixture_job):
    _add_carrier(fixture_job)
    class MislabelledSlicer(TrackingFakeSlicer):
        def slice(self, *args):
            output = super().slice(*args)
            return SliceOutput(output.path, False, output.metadata)
    result = _prepare(fixture_job, MislabelledSlicer())
    assert result['status'] == 'needs_input'
    assert result['issues'][-1]['code'] == 'SIMULATED_VERIFICATION'


@pytest.mark.parametrize('objects,ok', [
    (['base_stl.stl', 'lid_stl.stl', 'pcb_carrier_stl.stl'], True),
    (['base_stl.stl', 'lid_stl.stl'], False),
    (['base_stl.stl', 'lid_stl.stl', 'wrong.stl'], False),
    (['base_stl.stl', 'lid_stl.stl', 'pcb_carrier_stl.stl:skipped'], False),
])
def test_real_cli_path_forwards_three_inputs_and_checks_output_objects(fixture_job, monkeypatch, objects, ok):
    """Mock subprocess, not the BambuCliSlicer code; outputs remain synthetic test data."""
    f = fixture_job
    _add_carrier(f)
    verification = read_json(f['verification'])
    verification['simulation'] = False  # Synthetic protocol fixture, never an acceptance result.
    write_json(f['verification'], verification)
    binary = f['root'] / 'test-cli-placeholder'
    binary.write_text('Not executed; subprocess is mocked.')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if '--help' in args:
            return subprocess.CompletedProcess(args, 0, b'BambuStudio-test: --slice --load-settings --load-filaments --export-3mf --datadir')
        output_dir = Path(args[args.index('--outputdir') + 1])
        output = output_dir / args[args.index('--export-3mf') + 1]
        profiles = read_json(f['request'])['printing']['profiles']
        settings = {
            'printer_model': 'Bambu Lab A1', 'printer_settings_id': profiles['machine']['id'],
            'print_settings_id': profiles['process']['id'], 'filament_settings_id': [profiles['filament']['id']],
            'filament_type': ['PLA'], 'nozzle_diameter': ['0.4'], 'curr_bed_type': 'Textured PEI Plate',
        }
        nodes = []
        for name in objects:
            skipped = name.endswith(':skipped')
            name = name.removesuffix(':skipped')
            nodes.append('<object name="%s" skipped="%s"/>' % (name, 'true' if skipped else 'false'))
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('Metadata/plate_1.gcode', '; synthetic contract fixture, never executed\n')
            archive.writestr('Metadata/project_settings.config', json.dumps(settings))
            archive.writestr('Metadata/slice_info.config', '<config><plate>' + ''.join(nodes) + '</plate></config>')
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(subprocess, 'run', run)
    slicer = BambuCliSlicer(binary, executable_sha256=sha256(binary), version='test')
    result = _prepare(f, slicer)
    assert len(calls) == 2
    assert [Path(arg).name for arg in calls[-1][-3:]] == ['base_stl.stl', 'lid_stl.stl', 'pcb_carrier_stl.stl']
    if ok:
        assert result['status'] == 'ok' and result['simulation'] is False
        assert [ref['kind'] for ref in result['inputs']['models']] == ['base_stl', 'lid_stl', 'pcb_carrier_stl']
        assert len(result['inputs']['models']) == 3
    else:
        assert result['status'] == 'failed'
        assert result['issues'][-1]['code'] == 'SLICED_MODELS_MISSING'
