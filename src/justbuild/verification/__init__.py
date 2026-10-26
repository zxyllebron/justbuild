"""Independent STEP verification. Importing this module does not load the CAD kernel."""

from pathlib import Path


def verify(request_path: Path, cad_result_path: Path, output_dir: Path) -> Path:
    """Write a structured report; geometric failures are returned in that report."""
    from .engine import verify as run

    return run(Path(request_path), Path(cad_result_path), Path(output_dir))


__all__ = ["verify"]
