"""python -m justbuild.cad REQUEST --output-dir DIRECTORY"""
import argparse
import json
from pathlib import Path

from . import generate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = generate(args.request, args.output_dir)
    print(result)
    status = json.loads(result.read_text(encoding="utf-8"))["status"]
    return {"ok": 0, "needs_input": 2, "unsupported": 3, "failed": 1}[status]


if __name__ == "__main__":
    raise SystemExit(main())
