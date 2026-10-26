"""Lossless GLB display repacking: preserve triangles, reduce face draw calls.

This is a display derivative only. STEP and existing verification/slicing
artifacts remain authoritative and are never edited by this module.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import time

import numpy as np


class ReviewMeshError(ValueError):
    """The input cannot be repacked with the promised lossless guarantees."""


_TYPES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_DTYPES = {5120: np.dtype("i1"), 5121: np.dtype("u1"), 5122: np.dtype("<i2"),
           5123: np.dtype("<u2"), 5125: np.dtype("<u4"), 5126: np.dtype("<f4")}
_JSON, _BIN = 0x4E4F534A, 0x004E4942


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path):
    raw = path.read_bytes()
    if len(raw) < 20 or struct.unpack_from("<4sII", raw) != (b"glTF", 2, len(raw)):
        raise ReviewMeshError("Expected a complete GLB 2.0 file")
    chunks, cursor = {}, 12
    while cursor < len(raw):
        if cursor+8 > len(raw):
            raise ReviewMeshError("Truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", raw, cursor)
        cursor += 8
        if cursor+size > len(raw) or kind in chunks or size % 4:
            raise ReviewMeshError("Invalid or duplicate GLB chunk")
        chunks[kind] = raw[cursor:cursor+size]
        cursor += size
    if set(chunks) != {_JSON, _BIN}:
        raise ReviewMeshError("Only a JSON chunk and one embedded BIN chunk are supported")
    doc = json.loads(chunks[_JSON])
    if doc.get("asset", {}).get("version") != "2.0" or doc.get("extensionsUsed") or doc.get("extensionsRequired"):
        raise ReviewMeshError("Only uncompressed glTF 2.0 without extension payloads is supported")
    if any(doc.get(name) for name in ("images", "textures", "skins", "animations")):
        raise ReviewMeshError("Embedded images, skinning and animation require a separate preservation implementation")
    buffers = doc.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0] or buffers[0]["byteLength"] > len(chunks[_BIN]):
        raise ReviewMeshError("Expected one complete embedded GLB buffer")
    return doc, chunks[_BIN], len(chunks[_JSON]), len(chunks[_BIN])


def _array(doc, binary, index):
    accessor = doc["accessors"][index]
    if "sparse" in accessor or accessor.get("type") not in _TYPES or accessor.get("componentType") not in _DTYPES:
        raise ReviewMeshError("Unsupported accessor format")
    view = doc["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ReviewMeshError("External GLB buffer")
    count, columns = accessor["count"], _TYPES[accessor["type"]]
    dtype = _DTYPES[accessor["componentType"]]
    row_bytes = columns*dtype.itemsize
    stride = view.get("byteStride", row_bytes)
    offset = accessor.get("byteOffset", 0)
    if (not isinstance(count, int) or count <= 0 or offset < 0 or stride < row_bytes
        or offset+(count-1)*stride+row_bytes > view["byteLength"]):
        raise ReviewMeshError("Accessor exceeds its buffer view")
    absolute = view.get("byteOffset", 0)+offset
    if absolute+(count-1)*stride+row_bytes > len(binary):
        raise ReviewMeshError("Accessor exceeds the GLB BIN chunk")
    result = np.ndarray((count, columns), dtype=dtype, buffer=binary, offset=absolute,
                        strides=(stride, dtype.itemsize)).copy()
    if np.issubdtype(dtype, np.floating) and not np.isfinite(result).all():
        raise ReviewMeshError("Non-finite mesh attribute")
    return result


def _format(doc, index):
    a = doc["accessors"][index]
    return a["componentType"], a["type"], bool(a.get("normalized", False))


def _groups(doc, binary, mesh):
    groups = OrderedDict()
    for primitive in mesh["primitives"]:
        if primitive.get("mode", 4) != 4 or primitive.get("targets") or primitive.get("extensions"):
            raise ReviewMeshError("Only triangle meshes without morph/extension payloads are supported")
        attributes = primitive["attributes"]
        if "POSITION" not in attributes or _format(doc, attributes["POSITION"])[:2] != (5126, "VEC3"):
            raise ReviewMeshError("A FLOAT VEC3 POSITION attribute is required")
        formats = tuple((name, *_format(doc, index)) for name, index in sorted(attributes.items()))
        shared = {k: deepcopy(v) for k, v in primitive.items() if k not in ("attributes", "indices")}
        shared["mode"] = 4
        key = (json.dumps(shared, sort_keys=True, separators=(",", ":")), formats)
        arrays = {name: _array(doc, binary, index) for name, index in attributes.items()}
        count = len(arrays["POSITION"])
        if any(len(value) != count for value in arrays.values()):
            raise ReviewMeshError("Primitive vertex attributes have different counts")
        if "indices" in primitive:
            index_format = _format(doc, primitive["indices"])
            if index_format[0] not in (5121, 5123, 5125) or index_format[1:] != ("SCALAR", False):
                raise ReviewMeshError("Invalid triangle index accessor")
            indices = _array(doc, binary, primitive["indices"])[:, 0].astype(np.int64)
        else:
            indices = np.arange(count, dtype=np.int64)
        if len(indices) % 3 or indices.min() < 0 or indices.max() >= count:
            raise ReviewMeshError("Invalid or out-of-range triangle indices")
        item = groups.setdefault(key, {"shared": shared, "formats": formats, "parts": [], "vertices": 0})
        item["parts"].append((arrays, indices+item["vertices"]))
        item["vertices"] += count
    for group in groups.values():
        yield {"shared": group["shared"], "formats": group["formats"],
               "attributes": {name: np.concatenate([a[name] for a, _ in group["parts"]])
                              for name, *_ in group["formats"]},
               "indices": np.concatenate([i for _, i in group["parts"]])}


def _payload(groups):
    digest = hashlib.sha256()
    triangles, vertices = 0, 0
    low, high = np.full(3, np.inf), np.full(3, -np.inf)
    for group in groups:
        indices = group["indices"]
        triangles += len(indices)//3
        vertices += len(group["attributes"]["POSITION"])
        positions = group["attributes"]["POSITION"]
        low, high = np.minimum(low, positions.min(axis=0)), np.maximum(high, positions.max(axis=0))
        digest.update(json.dumps([group["shared"], group["formats"]], sort_keys=True, separators=(",", ":")).encode())
        for name, *_ in group["formats"]:
            digest.update(name.encode()+b"\0")
            digest.update(group["attributes"][name][indices].tobytes())
    return {"triangle_payload_sha256": digest.hexdigest(), "triangles": triangles, "vertices": vertices,
            "local_bounds": {"min": low.tolist(), "max": high.tolist()}}


class _Buffer:
    def __init__(self):
        self.data, self.views, self.accessors, self.reused = bytearray(), [], [], 0
        self.cache = {}

    def add(self, array, format_, target):
        component, type_, normalized = format_
        raw = np.ascontiguousarray(array, dtype=_DTYPES[component]).tobytes()
        key = (format_, target, raw)
        if key in self.cache:
            self.reused += 1
            return self.cache[key]
        self.data.extend(b"\0"*((-len(self.data)) % 4))
        view = len(self.views)
        self.views.append({"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw), "target": target})
        self.data.extend(raw)
        accessor = {"bufferView": view, "byteOffset": 0, "componentType": component,
                    "count": len(array), "type": type_}
        if normalized:
            accessor["normalized"] = True
        if type_ == "VEC3" and component == 5126 or target == 34963:
            accessor["min"], accessor["max"] = array.min(axis=0).tolist(), array.max(axis=0).tolist()
        index = len(self.accessors)
        self.accessors.append(accessor)
        self.cache[key] = index
        return index


def _deduplicate(group):
    attributes = group["attributes"]
    names = [name for name, *_ in group["formats"]]
    rows = np.concatenate([attributes[name].view(np.uint8).reshape(len(attributes[name]), -1) for name in names], axis=1)
    records = np.ascontiguousarray(rows).view(np.dtype((np.void, rows.shape[1]))).reshape(-1)
    _, first, inverse = np.unique(records, return_index=True, return_inverse=True)
    order = np.argsort(first)
    remap = np.empty(len(order), dtype=np.int64)
    remap[order] = np.arange(len(order))
    selected = first[order]
    return {name: attributes[name][selected] for name in names}, remap[inverse][group["indices"]]


def _node_world_matrices(doc):
    nodes = doc.get("nodes", [])
    result, active = {}, set()

    def local(node):
        if "matrix" in node:
            return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4, order="F")
        x, y, z, w = node.get("rotation", [0, 0, 0, 1])
        rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                             [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                             [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
        matrix = np.eye(4)
        matrix[:3, :3] = rotation @ np.diag(node.get("scale", [1, 1, 1]))
        matrix[:3, 3] = node.get("translation", [0, 0, 0])
        return matrix

    def visit(index, parent):
        if index in active or index in result:
            raise ReviewMeshError("Node graph contains a cycle or multiple parents")
        active.add(index)
        result[index] = parent @ local(nodes[index])
        for child in nodes[index].get("children", []):
            visit(child, result[index])
        active.remove(index)

    children = {i for node in nodes for i in node.get("children", [])}
    for index in range(len(nodes)):
        if index not in children:
            visit(index, np.eye(4))
    if len(result) != len(nodes):
        raise ReviewMeshError("Unreachable node cycle")
    return result


def _inspect(doc, binary):
    worlds = _node_world_matrices(doc)
    mesh_nodes = {}
    for index, node in enumerate(doc.get("nodes", [])):
        if "mesh" in node:
            if "skin" in node or node.get("weights"):
                raise ReviewMeshError("Skinned/morphed node is unsupported")
            mesh_nodes.setdefault(node["mesh"], []).append(index)
    records = []
    world_low, world_high = np.full(3, np.inf), np.full(3, -np.inf)
    for index, mesh in enumerate(doc["meshes"]):
        groups = list(_groups(doc, binary, mesh))
        item = _payload(groups)
        item.update(mesh_index=index, name=mesh.get("name", ""), primitive_count=len(mesh["primitives"]))
        records.append(item)
        for group in groups:
            positions = group["attributes"]["POSITION"].astype(np.float64)
            for node_index in mesh_nodes.get(index, []):
                matrix = worlds[node_index]
                transformed = positions @ matrix[:3, :3].T+matrix[:3, 3]
                world_low = np.minimum(world_low, transformed.min(axis=0))
                world_high = np.maximum(world_high, transformed.max(axis=0))
    return {"meshes": len(doc["meshes"]), "nodes": len(doc.get("nodes", [])),
            "primitives": sum(r["primitive_count"] for r in records),
            "triangles": sum(r["triangles"] for r in records), "vertices": sum(r["vertices"] for r in records),
            "accessors": len(doc.get("accessors", [])), "per_mesh": records,
            "world_bounds_m": {"min": world_low.tolist(), "max": world_high.tolist()}}


def _component_mapping(path, doc):
    if path is None:
        return []
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    mesh_names = {mesh.get("name"): index for index, mesh in enumerate(doc["meshes"])}
    output = []
    for feature in mapping["features"]:
        solids = feature["solid_indices"]
        names = [f"input_solid_{index}" for index in solids]
        if not names or any(name not in mesh_names for name in names):
            raise ReviewMeshError(f"Named component {feature.get('reference')} is missing source solid meshes")
        meshes = [mesh_names[name] for name in names]
        nodes = [i for i, node in enumerate(doc.get("nodes", [])) if node.get("mesh") in meshes]
        output.append({"reference": feature.get("reference"), "id": feature.get("id"), "label": feature.get("label"),
                       "source_solid_indices": solids, "mesh_names": names, "mesh_indices": meshes,
                       "node_indices": nodes, "preserved": True})
    return output


def _write_glb(path, doc, binary):
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    text += b" "*((-len(text)) % 4)
    binary = bytes(binary)+b"\0"*((-len(binary)) % 4)
    raw = (struct.pack("<4sII", b"glTF", 2, 12+8+len(text)+8+len(binary))
           +struct.pack("<II", len(text), _JSON)+text+struct.pack("<II", len(binary), _BIN)+binary)
    path.write_bytes(raw)


def optimize_review_glb(source_glb: Path, output_dir: Path, *, component_map_path: Path | None = None,
                        job_id: str = "review-mesh") -> Path:
    """Return a result JSON after validating a byte-exact display derivative.

    Raises ReviewMeshError for unsupported or unverifiable input. A caller may
    retain the original GLB as its fallback; no partial success is advertised.
    """
    started = time.monotonic()
    source_glb, output_dir = Path(source_glb).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    light, result_path = output_dir/"review-light.glb", output_dir/"review-mesh-result.json"
    protected = {source_glb}
    if component_map_path is not None:
        protected.add(Path(component_map_path).resolve())
    if light in protected or result_path in protected:
        raise ReviewMeshError("The output would overwrite an input")
    source_sha = _sha(source_glb)
    map_sha = _sha(Path(component_map_path)) if component_map_path else None
    doc, binary, source_json_bytes, source_bin_bytes = _load(source_glb)
    before = _inspect(doc, binary)
    buffer = _Buffer()
    packed = deepcopy(doc)
    for mesh in packed["meshes"]:
        primitives = []
        for group in _groups(doc, binary, mesh):
            attributes, indices = _deduplicate(group)
            primitive = deepcopy(group["shared"])
            primitive["attributes"] = {name: buffer.add(attributes[name], tuple(format_), 34962)
                                       for name, *format_ in group["formats"]}
            maximum = int(indices.max())
            component = 5121 if maximum <= 255 else 5123 if maximum <= 65535 else 5125
            primitive["indices"] = buffer.add(indices.reshape(-1, 1), (component, "SCALAR", False), 34963)
            primitives.append(primitive)
        mesh["primitives"] = primitives
    packed["accessors"], packed["bufferViews"] = buffer.accessors, buffer.views
    packed["buffers"] = [{"byteLength": len(buffer.data)}]
    descriptor, temporary = tempfile.mkstemp(prefix=".review-light-", suffix=".glb", dir=output_dir)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        _write_glb(temporary, packed, buffer.data)
        reread, new_binary, json_bytes, bin_bytes = _load(temporary)
        after = _inspect(reread, new_binary)
        for key in ("nodes", "materials", "scenes", "scene"):
            if doc.get(key) != reread.get(key):
                raise ReviewMeshError(f"Preserved scene field changed: {key}")
        if before["meshes"] != after["meshes"] or before["nodes"] != after["nodes"]:
            raise ReviewMeshError("Mesh/node inventory changed")
        preservation = []
        for a, b in zip(before["per_mesh"], after["per_mesh"]):
            if any(a[key] != b[key] for key in ("name", "triangles", "triangle_payload_sha256", "local_bounds")):
                raise ReviewMeshError(f"Lossless preservation failed for mesh {a['mesh_index']}")
            preservation.append({"mesh_index": a["mesh_index"], "name": a["name"], "triangles": a["triangles"],
                                 "primitives_before": a["primitive_count"], "primitives_after": b["primitive_count"],
                                 "triangle_payload_sha256": a["triangle_payload_sha256"], "preserved": True})
        if not np.allclose([*before["world_bounds_m"].values()], [*after["world_bounds_m"].values()], rtol=0, atol=1e-12):
            raise ReviewMeshError("World frame/bounds changed")
        components = _component_mapping(component_map_path, reread)
        if _sha(source_glb) != source_sha:
            raise ReviewMeshError("Source GLB changed while it was being repacked")
        if component_map_path and _sha(Path(component_map_path)) != map_sha:
            raise ReviewMeshError("Component mapping changed while the GLB was being repacked")
        source_bytes, light_bytes = source_glb.stat().st_size, temporary.stat().st_size
        if light_bytes >= source_bytes:
            raise ReviewMeshError("Lossless repacking did not reduce this file; retain the original preview")
        os.replace(temporary, light)
        result = {"schema_version": "0.1", "job_id": job_id, "status": "ok", "issues": [],
                  "artifacts": [{"kind": "review_glb_light", "path": light.name, "sha256": _sha(light),
                                 "bytes": light_bytes, "units": "m"}],
                  "metadata": {"display_only": True, "method": "lossless_primitive_merge_and_bitwise_vertex_attribute_deduplication",
                               "source_path": str(source_glb), "source_sha256": source_sha,
                               "source_bytes": source_bytes, "light_bytes": light_bytes,
                               "bytes_reduction_fraction": 1-light_bytes/source_bytes,
                               "source_json_bytes": source_json_bytes, "light_json_bytes": json_bytes,
                               "source_bin_bytes": source_bin_bytes, "light_bin_bytes": bin_bytes,
                               "before": {k: v for k, v in before.items() if k != "per_mesh"},
                               "after": {k: v for k, v in after.items() if k != "per_mesh"},
                               "mesh_preservation": preservation, "component_mapping": components,
                               "component_map_path": str(Path(component_map_path).resolve()) if component_map_path else None,
                               "component_map_sha256": map_sha,
                               "named_component_count": len(components), "nodes_materials_and_transforms_unchanged": True,
                               "max_added_position_error_mm": 0.0, "error_baseline": "original GLB triangle-corner payload, not exact STEP surfaces",
                               "normals_preserved": True, "geometry_simplification": False,
                               "shared_accessor_reuses": buffer.reused, "elapsed_seconds": time.monotonic()-started,
                               "geometry_authority": "Original STEP and its existing verification; this derivative is for display only"}}
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return result_path
    finally:
        temporary.unlink(missing_ok=True)
