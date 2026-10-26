"""Hash-bound reuse of unchanged source checks and explicit identity byte export.

Only input-solid validity is reusable; all newly generated geometry and services
are checked against the freshly imported original BRep on every run.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from .request import need


def digest(path):
    d=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):d.update(data)
    return d.hexdigest()


def reuse_input_validation(cache, config, source, solids, result, cq_version):
    """Return prior input measurements only after caller-pinned cache verification."""
    if cache is None:return None
    need(isinstance(cache,dict) and isinstance(cache.get('sha256'),str)
         and len(cache['sha256'])==64 and cache.get('result_path'), 'Invalid source validation cache descriptor')
    path=Path(cache['result_path']).resolve()
    need(digest(path)==cache['sha256'],'Source validation cache result hash changed')
    old=json.loads(path.read_text());md=old.get('metadata',{});prov=md.get('input_provenance',{})
    import OCP
    ocp_version=OCP.__version__
    recorded_ocp=md.get('kernel',{}).get('ocp_version')
    need('ocp_version' not in cache or cache['ocp_version']==ocp_version, 'Caller-pinned OCP version differs from current runtime')
    need((recorded_ocp or cache.get('ocp_version'))==ocp_version,
         'Source cache requires matching recorded or explicitly caller-pinned OCP version')
    identity={'origin_mm':[0,0,0],'x_axis':[1,0,0],'y_axis':[0,1,0],'z_axis':[0,0,1]}
    frame=config['request']['constraints']['board_frame']
    need(old.get('status')=='ok' and md.get('units')=='mm'
         and md.get('kernel',{}).get('cadquery_version')==cq_version
         and md.get('board_placement',{}).get('frame')==frame==identity
         and prov.get('step_sha256')==digest(source),
         'Source validation cache requires identical source bytes, frame, units and kernel version')
    for kind in ('source_step','normalized_input_step'):
        entries=[a for a in old.get('artifacts',[]) if a.get('kind')==kind]
        need(len(entries)==1,'Source cache lacks required bound original artifact',artifact=kind)
        item=entries[0];artifact_path=(path.parent/item['path']).resolve()
        need(digest(artifact_path)==item.get('sha256')==prov['step_sha256'],
             'Source cache original/normalized bytes changed',artifact=kind)
    from .recovery import native_mm_units
    units=native_mm_units(source);config['_native_mm_units']=units
    rows=md.get('input_solids',[])
    need(len(rows)==len(solids),'Source cache solid count differs from actual import')
    checks=[];values=[]
    for i,row in enumerate(rows):
        matching=[c for c in old.get('checks',[]) if c.get('name')==f'input_solid_{i}_valid_single_solid']
        need(len(matching)==1 and matching[0].get('status')=='pass','Source cache lacks a passing input-solid validity check',solid_index=i)
        original=matching[0];m=original.get('measurements',{})
        need(row.get('index')==i and m.get('is_valid') is True and m.get('solid_count')==1
             and row.get('bounds')==m.get('bounds') and row.get('volume_mm3')==m.get('volume_mm3'),
             'Source cache validity measurements disagree with its inventory',solid_index=i)
        c=deepcopy(original);c['reused_check_provenance']={'result_path':str(path),'result_sha256':cache['sha256'],
            'same_source_sha256':prov['step_sha256'],'same_frame':frame,
            'ocp_version':ocp_version,'ocp_evidence':'prior result' if recorded_ocp else 'explicit caller-pinned same-runtime attestation; old result omitted OCP version',
            'scope':'unchanged input-solid validation only; new case geometry is not reused'}
        checks.append(c);values.append(deepcopy(m))
    result['checks'].extend(checks)
    result['metadata']['source_validation_cache']={'result_path':str(path),'result_sha256':cache['sha256'],
        'source_sha256':prov['step_sha256'],'reused_input_checks':len(checks),
        'fresh_source_import':True,'new_case_checks_reused':False,'native_mm_units':units,
        'ocp_version':ocp_version,'ocp_evidence':'prior result' if recorded_ocp else 'caller-pinned same-runtime attestation'}
    return values


def export_identity_source(config, source, out, result, artifacts):
    """Preserve already checked native-mm geometry without lossy reencoding."""
    from .recovery import native_mm_units
    identity={'origin_mm':[0,0,0],'x_axis':[1,0,0],'y_axis':[0,1,0],'z_axis':[0,0,1]}
    request=config['request'];need(request['constraints']['board_frame']==identity,'Source byte export requires exact identity placement')
    units=config.get('_native_mm_units') or native_mm_units(source);normalized=out/'normalized_input.step';shutil.copyfile(source,normalized)
    original=digest(source);need(digest(normalized)==original,'Preserved source bytes changed')
    result['checks'].append({'name':'normalized_input_byte_preservation','status':'pass',
        'method':'SHA-256 byte equality with original source, exact identity frame and native-mm units',
        'tolerance':{'byte_difference':0},'measurements':{'source_sha256':original,'length_units':units,
        'source_solid_count':len(result['metadata']['input_solids'])}})
    result['metadata']['source_export']={'policy':'native_identity_bytes','length_units':units,
        'source_sha256':original,'assembly_step':'omitted by explicit source_export_mode; no source reencoding attempted'}
    artifacts.append(('normalized_input_step',normalized,'mm'))
    kinds={'normalized_input_step','base_step','lid_step','pcb_carrier_step','screen_envelope_step'}
    manifest={'schema_version':'0.1','job_id':request['job_id'],'units':'mm','source_preserved_byte_for_byte':True,
              'frame':identity,'parts':[{'kind':kind,'path':path.name,'sha256':digest(path),
                'transform':'identity; same board coordinate system',
                **({'manufacturer_component_model':False} if kind=='screen_envelope_step' else {})}
                for kind,path,unit in artifacts if kind in kinds]}
    path=out/'assembly_manifest.json';path.write_text(json.dumps(manifest,indent=2)+'\n')
    artifacts.append(('assembly_manifest',path,None))
