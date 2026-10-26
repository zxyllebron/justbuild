"""Explicit synthetic PCB/connector, never measurements of a user's board."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def make_fixture(output_dir: Path) -> Path:
    import cadquery as cq

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pcb = cq.Solid.makeBox(60, 40, 1.6)
    for x, y in ((5, 5), (55, 5), (5, 35), (55, 35)):
        pcb = pcb.cut(cq.Solid.makeCylinder(1.6, 3.6, cq.Vector(x, y, -1)))
    connector = cq.Solid.makeBox(10, 10, 5, cq.Vector(54, 15, 1.6))
    # A compound preserves two genuine solids. Their import indices are verified in tests.
    cq.exporters.export(cq.Compound.makeCompound([pcb, connector]), str(output_dir / "synthetic_board.step"))
    request = json.loads(Path(__file__).with_name("example_request.json").read_text(encoding="utf-8"))
    path = output_dir / "request.json"
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(make_fixture(args.output_dir))
