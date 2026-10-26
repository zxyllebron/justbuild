"""Headless command-line adapter for the public verification entry point."""

import argparse
import json
from pathlib import Path

from . import verify


def main():
    parser = argparse.ArgumentParser(description="Independently verify PCB enclosure STEP solids")
    parser.add_argument("request", type=Path)
    parser.add_argument("cad_result", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result_path = verify(args.request, args.cad_result, args.output_dir)
    report = json.loads(result_path.read_text(encoding="utf-8"))
    print(result_path)
    return 0 if report["verdict"] == "pass" else 1 if report["verdict"] == "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
