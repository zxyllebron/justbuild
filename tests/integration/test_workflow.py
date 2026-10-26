"""Real CAD integration and web authorization boundaries; no printer/network provider calls."""
from pathlib import Path
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from justbuild.contracts import read_json
from justbuild.demo import create_demo
from justbuild.server import create_app


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    root = tmp_path_factory.mktemp("justbuild-integration")
    app = create_app(root / "jobs")
    client = TestClient(app)
    client.get("/api/health")
    job = create_demo(app.state.manager, wait=True)
    token = client.cookies["justbuild_session"]
    job = app.state.manager.update(job["job_id"],owner_sha256=hashlib.sha256(token.encode()).hexdigest(),review_token="test-read-only-capability")
    yield app,client,job
    app.state.manager.pool.shutdown(wait=True)


def test_real_step_generation_and_independent_checks(reference):
    app,client,job = reference
    assert job["synthetic"] is True
    assert job["state"] in ("review_ready","ready_to_print"), job["issues"]
    cad = job["stage_results"]["generating"]["result"]
    verification = job["stage_results"]["verifying"]["result"]
    assert cad["status"] == "ok"
    assert verification["verdict"] == "pass",verification
    assert any(c["id"] == "side_wall_clearance" and c["status"] == "pass" for c in verification["checks"])
    assert any(c["id"] == "floor_clearance" and c["status"] == "pass" for c in verification["checks"])
    assert all(c["status"] in ("pass","not_applicable") for c in verification["checks"] if c["required"])
    assert any(c["category"] == "thermal" and c["status"] == "not_checked" for c in verification["checks"])
    kinds = {a["kind"] for a in job["artifacts"]}
    assert {"base_step","lid_step","review_glb","base_stl","lid_stl"} <= kinds


def test_downloads_bound_to_actual_bytes(reference):
    app,client,job = reference
    for artifact in job["artifacts"]:
        response = client.get(artifact["url"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]


def test_another_session_cannot_see_or_mutate_private_job(reference):
    app,client,job = reference
    other = TestClient(app)
    assert other.get(f"/api/jobs/{job['job_id']}").status_code == 404
    assert other.post(f"/api/jobs/{job['job_id']}/print").status_code == 404
    assert other.post(f"/api/jobs/{job['job_id']}/handoff").status_code == 404
    assert not any(x["job_id"]==job["job_id"] for x in other.get('/api/jobs').json())


def test_share_capability_is_read_only(reference):
    app,client,job = reference
    other = TestClient(app)
    headers = {"X-Review-Token":"test-read-only-capability"}
    response = other.get(f"/api/jobs/{job['job_id']}",headers=headers)
    assert response.status_code == 200
    assert response.json()["can_edit"] is False
    assert "owner_sha256" not in response.json()
    assert other.post(f"/api/jobs/{job['job_id']}/print",headers=headers).status_code == 404
    assert other.post(f"/api/jobs/{job['job_id']}/handoff",headers=headers).status_code == 404


def test_unconfigured_handoff_has_no_public_ticket(reference):
    app,client,job = reference
    assert client.post(f"/api/jobs/{job['job_id']}/handoff").status_code == 503


def test_held_design_cannot_start_printing(reference):
    app,client,job = reference
    old = app.state.manager.get(job['job_id'])
    app.state.manager.update(job['job_id'],print_hold={"reason":"redesign_requires_review"},state="ready_to_print")
    try:
        response = client.post(f"/api/jobs/{job['job_id']}/print")
        assert response.status_code == 409
        assert "held" in response.json()['detail']
    finally:
        app.state.manager.update(job['job_id'],print_hold=old.get('print_hold'),state=old['state'])


def test_real_printing_never_starts_in_mvp(reference):
    app,client,job = reference
    response = client.post(f"/api/jobs/{job['job_id']}/print")
    assert response.status_code in (409,501)


def test_cross_origin_mutation_is_rejected(reference):
    app,client,job = reference
    assert client.post('/api/demo',headers={"Origin":"https://unrelated.invalid"}).status_code == 403


def test_arbitrary_non_step_upload_is_rejected(reference):
    app,client,job = reference
    assert client.post('/api/jobs',files={"board":("bad.step",b"this is not a STEP")}).status_code == 400
    assert client.post('/api/jobs',files={"board":("program.py",b"print('no')")}).status_code == 400


def test_viewer_assets_are_served_without_cdn(reference):
    app,client,job = reference
    assert client.get('/').status_code == 200
    for name in ('app.js','software-renderer.js','software-rasterizer.js','vendor/three.module.js','vendor/three.core.js','vendor/GLTFLoader.js','vendor/SkeletonUtils.js'):
        assert client.get('/static/'+name).status_code == 200


def test_unconfirmed_request_cannot_enter_pipeline(reference):
    app,client,job = reference
    source = app.state.manager.directory(job['job_id'])/'input'/'board.step'
    new = app.state.manager.create(source,label='confirmation test')
    with pytest.raises(ValueError,match='explicitly true'):
        app.state.manager.confirm(new['job_id'],new['proposal']['request'])


def test_changed_artifact_is_rejected(reference):
    app,client,job = reference
    item = next(x for x in job['artifacts'] if x['kind']=='base_stl')
    stage = job['stage_results'][item['stage']]
    result_path = app.state.manager.directory(job['job_id'])/stage['path']
    ref = next(x for x in stage['result']['artifacts'] if x['kind']=='base_stl')
    path = result_path.parent/ref['path']
    original = path.read_bytes()
    try:
        path.write_bytes(original+b'tamper')
        assert client.get(item['url']).status_code == 404
    finally:
        path.write_bytes(original)


def test_lossless_display_stage_is_automatic_and_does_not_change_cad(reference):
    app, client, job = reference
    derived = job['stage_results']['review_model']['result']
    assert derived['status'] == 'ok'
    artifact = next(a for a in job['artifacts'] if a['kind'] == 'review_glb_light')
    original = next(a for a in job['artifacts'] if a['kind'] == 'review_glb')
    assert artifact['sha256'] != original['sha256']
    before = app.state.manager.get(job['job_id'])
    app.state.manager.register_stage_result(job['job_id'], 'review_model',
        app.state.manager.directory(job['job_id'])/before['stage_results']['review_model']['path'])
    after = app.state.manager.get(job['job_id'])
    assert before['state'] == after['state']
    assert before['stage_results']['generating'] == after['stage_results']['generating']
    assert sum(a['kind'] == 'review_glb_light' for a in after['artifacts']) == 1


def test_cache_is_hash_bound_and_still_authorized(reference):
    app, client, job = reference
    artifact = next(a for a in job['artifacts'] if a['kind'] == 'review_glb_light')
    path = artifact['url']+'?sha='+artifact['sha256']
    response = client.get(path)
    assert response.status_code == 200
    assert 'private' in response.headers['cache-control']
    assert 'immutable' in response.headers['cache-control']
    tag = response.headers['etag']
    assert client.get(path, headers={'If-None-Match':tag}).status_code == 304
    assert TestClient(app).get(path, headers={'If-None-Match':tag}).status_code == 404
    assert client.get(artifact['url']+'?sha='+'0'*64).status_code == 409


def test_local_pairing_is_explicitly_disabled_by_default(reference):
    app, client, job = reference
    assert client.get('/api/health').json()['local_printer_setup_enabled'] is False
    assert client.get('/printer-setup').status_code == 404
    assert client.get('/static/printer-setup.js').status_code == 404
