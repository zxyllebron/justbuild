"""Lossless display repacking with independent small GLB fixtures; no CAD run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import pytest

from justbuild.review_mesh import ReviewMeshError, _array, _load, optimize_review_glb


def fixture_glb(path: Path, *, interleaved=False, materials_split=False, sharp_normals=False,
                invalid_indices=False, extension=False, flat_colors=False):
    binary, views, accessors = bytearray(), [], []

    def block(data, target):
        binary.extend(b"\0"*((-len(binary)) % 4))
        index = len(views)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data), "target": target})
        binary.extend(data)
        return index

    def accessor(view, component, count, type_, offset=0, normalized=False):
        index = len(accessors)
        accessors.append({"bufferView": view, "componentType": component, "count": count,
                          "type": type_, "byteOffset": offset, **({"normalized": True} if normalized else {})})
        return index

    names = ["base", "lid", "input_solid_0", "input_solid_1"]
    meshes = []
    for mesh_index, name in enumerate(names):
        primitives = []
        for face in range(8):
            positions = np.array([[0, 0, mesh_index], [1, 0, mesh_index], [0, 1, mesh_index]], dtype="<f4")
            normal = -1 if sharp_normals and face % 2 else 1
            normals = np.tile(np.array([[0, 0, normal]], dtype="<f4"), (3, 1))
            if interleaved:
                view = block(np.concatenate([positions, normals], axis=1).tobytes(), 34962)
                views[view]["byteStride"] = 24
                pos = accessor(view, 5126, 3, "VEC3")
                norm = accessor(view, 5126, 3, "VEC3", 12)
            else:
                pos = accessor(block(positions.tobytes(), 34962), 5126, 3, "VEC3")
                norm = accessor(block(normals.tobytes(), 34962), 5126, 3, "VEC3")
            colors = np.tile(np.array([[255, 0 if flat_colors else face, 0, 255]], dtype="u1"), (3, 1))
            color = accessor(block(colors.tobytes(), 34962), 5121, 3, "VEC4", normalized=True)
            indices = np.array([0, 1, 10 if invalid_indices else 2], dtype="<u2")
            ind = accessor(block(indices.tobytes(), 34963), 5123, 3, "SCALAR")
            primitives.append({"attributes": {"POSITION": pos, "NORMAL": norm, "COLOR_0": color},
                               "indices": ind, "material": face % 2 if materials_split else 0, "mode": 4})
        meshes.append({"name": name, "primitives": primitives})
    doc = {"asset": {"version": "2.0", "generator": "independent-test-fixture"},
           "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
           "materials": [{"doubleSided": True, "pbrMetallicRoughness": {"baseColorFactor": [0, 0, 1, 0.5]}, "alphaMode": "BLEND"},
                         {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}],
           "meshes": meshes,
           "nodes": [{"name": "root", "children": [1, 2, 3, 4], "scale": [0.001]*3, "translation": [1, 2, 3]},
                     *[{"name": name, "mesh": i} for i, name in enumerate(names)]],
           "scene": 0, "scenes": [{"nodes": [0]}]}
    if extension:
        doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]
    text = json.dumps(doc, indent=2).encode()
    text += b" "*((-len(text)) % 4)
    data = bytes(binary)+b"\0"*((-len(binary)) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28+len(text)+len(data))
                     +struct.pack("<II", len(text), 0x4E4F534A)+text
                     +struct.pack("<II", len(data), 0x004E4942)+data)
    return doc


def test_lossless_repack_retains_all_meshes_materials_normals_and_frame(tmp_path):
    source = tmp_path/"full.glb"
    original = fixture_glb(source, sharp_normals=True, flat_colors=True)
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    result = json.loads(optimize_review_glb(source, tmp_path/"light").read_text())
    metadata = result["metadata"]
    assert metadata["after"]["triangles"] == metadata["before"]["triangles"] == 32
    assert metadata["after"]["meshes"] == 4 and metadata["after"]["nodes"] == 5
    assert metadata["before"]["vertices"] == 96 and metadata["after"]["vertices"] == 24
    assert metadata["after"]["primitives"] == 4 < metadata["before"]["primitives"] == 32
    assert metadata["max_added_position_error_mm"] == 0
    assert metadata["geometry_simplification"] is False and metadata["normals_preserved"] is True
    assert metadata["light_bytes"] < metadata["source_bytes"]
    assert all(m["preserved"] for m in metadata["mesh_preservation"])
    assert metadata["after"]["world_bounds_m"]["min"] == pytest.approx([1, 2, 3])
    assert metadata["after"]["world_bounds_m"]["max"] == pytest.approx([1.001, 2.001, 3.003])
    packed, binary, *_ = _load(tmp_path/"light/review-light.glb")
    assert packed["nodes"] == original["nodes"] and packed["materials"] == original["materials"]
    primitive = packed["meshes"][0]["primitives"][0]
    normals = _array(packed, binary, primitive["attributes"]["NORMAL"])
    assert set(normals[:, 2]) == {-1, 1}
    assert packed["accessors"][primitive["attributes"]["COLOR_0"]]["normalized"] is True
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha


def test_interleaved_attributes_and_material_partitions_are_preserved(tmp_path):
    source = tmp_path/"full.glb"
    fixture_glb(source, interleaved=True, materials_split=True)
    result = json.loads(optimize_review_glb(source, tmp_path/"light").read_text())
    assert result["metadata"]["after"]["primitives"] == 8
    packed, *_ = _load(tmp_path/"light/review-light.glb")
    assert {p["material"] for p in packed["meshes"][0]["primitives"]} == {0, 1}


def test_named_component_mapping_retains_every_member_solid(tmp_path):
    source = tmp_path/"full.glb"
    fixture_glb(source)
    mapping = tmp_path/"features.json"
    mapping.write_text(json.dumps({"features": [{"reference": "USB1", "id": "usb", "label": "USB", "solid_indices": [0, 1]}]}))
    result = json.loads(optimize_review_glb(source, tmp_path/"light", component_map_path=mapping).read_text())
    component = result["metadata"]["component_mapping"][0]
    assert component["source_solid_indices"] == [0, 1]
    assert component["mesh_names"] == ["input_solid_0", "input_solid_1"]
    assert component["mesh_indices"] == [2, 3] and component["node_indices"] == [3, 4]


def test_missing_named_component_fails_before_publishing(tmp_path):
    source = tmp_path/"full.glb"
    fixture_glb(source)
    mapping = tmp_path/"features.json"
    mapping.write_text(json.dumps({"features": [{"reference": "missing", "solid_indices": [99]}]}))
    with pytest.raises(ReviewMeshError, match="missing source solid"):
        optimize_review_glb(source, tmp_path/"light", component_map_path=mapping)
    assert not (tmp_path/"light/review-light.glb").exists()


@pytest.mark.parametrize("invalid_indices,extension,match", [(True, False, "indices"), (False, True, "extension")])
def test_unverifiable_input_never_becomes_a_light_success(tmp_path, invalid_indices, extension, match):
    source = tmp_path/"full.glb"
    fixture_glb(source, invalid_indices=invalid_indices, extension=extension)
    original = source.read_bytes()
    with pytest.raises(ReviewMeshError, match=match):
        optimize_review_glb(source, tmp_path/"light")
    assert source.read_bytes() == original
    assert not (tmp_path/"light/review-light.glb").exists()


def test_source_cannot_be_its_own_derived_output(tmp_path):
    source = tmp_path/"review-light.glb"
    fixture_glb(source)
    with pytest.raises(ReviewMeshError, match="overwrite an input"):
        optimize_review_glb(source, tmp_path)
