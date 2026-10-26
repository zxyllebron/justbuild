"""Local slicer boundary. No sending, login or printer commands exist here."""

from __future__ import annotations

import os
import json
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .common import PrintError, digest, read_json, sha256, write_json


@dataclass(frozen=True)
class SliceOutput:
    path: Path
    simulation: bool
    metadata: dict


class Slicer(Protocol):
    def slice(self, models: list[Path], profiles: dict[str, Path],
              settings: dict, output_dir: Path) -> SliceOutput: ...


def inspect_sliced_3mf(path: Path) -> dict:
    """Check container structure and nonempty G-code, not toolpath safety."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise PrintError("INVALID_3MF", "Duplicate archive members are not allowed.")
            members = [item for item in archive.infolist()
                       if re.fullmatch(r"Metadata/plate_[1-9][0-9]*\.gcode", item.filename)]
            if not members or any(item.file_size == 0 for item in members):
                raise PrintError("UNSLICED_3MF", "The 3MF contains no nonempty plate G-code.")
            # Bound work when inspecting untrusted ZIP files; never extract paths.
            if sum(item.file_size for item in archive.infolist()) > 1024 ** 3:
                raise PrintError("INVALID_3MF", "The 3MF exceeds the inspection size limit.")
            if archive.testzip() is not None:
                raise PrintError("INVALID_3MF", "The 3MF failed its ZIP CRC check.")
            return {"method": "ZIP CRC and nonempty plate G-code members",
                    "gcode_members": [item.filename for item in members],
                    "preview_images_available": any(name.lower().endswith(".png") for name in names),
                    "toolpath_safety": "not_checked", "units": "mm"}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PrintError("INVALID_3MF", "Slicer output is not a readable sliced 3MF.") from exc


class BambuCliSlicer:
    """Use an operator-configured, hash/version-pinned existing executable.

    Environment overrides are trusted deployment configuration, never request JSON.
    Child HOME/config/cache/datadir are private to this operation. Console text is
    intentionally not copied into result documents, as vendor output is untrusted.
    """

    def __init__(self, executable: Path, *, executable_sha256: str, version: str,
                 timeout_seconds: float = 300, environment: Mapping[str, str] | None = None):
        self.executable = Path(executable).resolve()
        self.expected_hash = digest(executable_sha256)
        self.version = version
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("Slicing timeout must be in (0, 3600].")
        self.timeout = timeout_seconds
        self.environment = dict(environment or {})

    def slice(self, models: list[Path], profiles: dict[str, Path],
              settings: dict, output_dir: Path) -> SliceOutput:
        if not self.executable.is_file():
            raise PrintError("SLICER_UNAVAILABLE", "Configured Bambu Studio executable is missing.", "unsupported")
        if sha256(self.executable) != self.expected_hash:
            raise PrintError("SLICER_HASH_MISMATCH", "Configured slicer executable has changed.")
        input_hashes = {path: sha256(path) for path in [*models, *profiles.values()]}
        output_dir.mkdir(parents=True, exist_ok=False)
        home = output_dir / "home"
        home.mkdir()
        # No account/token variables or existing desktop session are inherited.
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LOCPATH", "LD_LIBRARY_PATH")
               if key in os.environ}
        env.update(self.environment)
        env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
                    "XDG_CACHE_HOME": str(home / "cache"), "DISPLAY": "", "WAYLAND_DISPLAY": ""})
        common = [str(self.executable), "--datadir", str(home / "data")]

        def run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
            try:
                # Only --help output is captured; slicing output is discarded below.
                return subprocess.run(args, cwd=output_dir, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      timeout=timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise PrintError("SLICER_TIMEOUT", "Local slicing timed out; no submission occurred.") from exc
            except OSError as exc:
                raise PrintError("SLICER_UNAVAILABLE", "The configured CLI cannot be executed.", "unsupported") from exc

        help_result = run(common + ["--help"], min(20, self.timeout))
        help_text = help_result.stdout.decode("utf-8", errors="replace")
        if help_result.returncode != 0 or f"BambuStudio-{self.version}:" not in help_text:
            raise PrintError("SLICER_VERSION", "Slicer help did not confirm the pinned version.", "unsupported")
        if not all(flag in help_text for flag in ("--slice", "--load-settings", "--load-filaments", "--export-3mf", "--datadir")):
            raise PrintError("SLICER_CLI", "Required CLI capabilities are unavailable.", "unsupported")
        path = output_dir / "enclosure.gcode.3mf"
        command = common + ["--load-settings", f"{profiles['machine']};{profiles['process']}",
                            "--load-filaments", str(profiles["filament"]),
                            "--curr-bed-type", settings["bed_type"], "--arrange", "1",
                            "--orient", "0", "--slice", "0", "--debug", "1",
                            "--outputdir", str(output_dir), "--export-3mf", path.name]
        command += [str(model) for model in models]
        # Slicing may generate progress artifacts; stdout is unnecessary here.
        try:
            result = subprocess.run(command, cwd=output_dir, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise PrintError("SLICER_TIMEOUT", "Local slicing timed out; no submission occurred.") from exc
        except OSError as exc:
            raise PrintError("SLICER_UNAVAILABLE", "The configured CLI cannot be executed.", "unsupported") from exc
        if result.returncode != 0:
            raise PrintError("SLICER_FAILED", f"Bambu Studio exited with code {result.returncode}.")
        if sha256(self.executable) != self.expected_hash or any(sha256(path) != value for path, value in input_hashes.items()):
            raise PrintError("INPUT_CHANGED", "Slicer executable or input bytes changed during slicing.")
        inspection = inspect_sliced_3mf(path)
        if len(inspection["gcode_members"]) != 1:
            raise PrintError("MULTIPLE_PLATES", "Initial base/lid workflow requires one sliced plate.", "needs_input")
        try:
            with zipfile.ZipFile(path) as archive:
                effective = json.loads(archive.read("Metadata/project_settings.config"))
            selected = {role: read_json(profile) for role, profile in profiles.items()}
            expected = {"printer_model": selected["machine"]["printer_model"],
                        "printer_settings_id": selected["machine"]["name"],
                        "print_settings_id": selected["process"]["name"],
                        "filament_settings_id": [selected["filament"]["name"]],
                        "filament_type": selected["filament"]["filament_type"],
                        "nozzle_diameter": selected["machine"]["nozzle_diameter"],
                        "curr_bed_type": settings["bed_type"]}
            if any(effective.get(key) != value for key, value in expected.items()):
                raise PrintError("EFFECTIVE_SETTINGS_MISMATCH", "Sliced 3MF does not retain the selected A1/nozzle/material/profile identities.")
            inspection["effective_settings"] = expected
        except (ValueError, KeyError, TypeError) as exc:
            raise PrintError("SLICED_SETTINGS_MISSING", "Sliced 3MF lacks readable effective printer settings.") from exc
        write_json(output_dir / "invocation.json", {
            "executable_sha256": self.expected_hash, "version": self.version,
            "argv": command, "exit_code": result.returncode, "inspection": inspection,
        })
        return SliceOutput(path, False, {"adapter": "bambu_studio_cli", "version": self.version,
                                       "executable_sha256": self.expected_hash, "inspection": inspection,
                                       "orientation": "input mesh orientation; automatic orientation disabled"})


class FakeSlicer:
    """Contract tests only; deliberately produces no printable G-code."""

    def slice(self, models: list[Path], profiles: dict[str, Path],
              settings: dict, output_dir: Path) -> SliceOutput:
        output_dir.mkdir(parents=True, exist_ok=False)
        path = write_json(output_dir / "SIMULATION-not-printable.json", {
            "simulation": True, "models": [sha256(model) for model in models],
            "profiles": {key: sha256(value) for key, value in profiles.items()},
        })
        return SliceOutput(path, True, {"adapter": "fake", "printable": False})
