"""Disk-backed jobs and sequential geometry execution for a single-server MVP."""
from __future__ import annotations

import copy
import importlib
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .contracts import digest, now, read_json, resolve_artifact, write_json
from .inspection import inspect_step


class JobManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="justbuild-geometry")
        self.lock = threading.RLock()
        from .slicing_config import SlicingConfig
        self.slicing = SlicingConfig()

    def directory(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Invalid job ID")
        path = self.root / job_id
        if not path.is_dir():
            raise FileNotFoundError(job_id)
        return path

    def get(self, job_id: str) -> dict:
        return read_json(self.directory(job_id) / "job.json")

    def list(self) -> list[dict]:
        jobs = []
        for path in self.root.glob("*/job.json"):
            try:
                job = read_json(path)
                jobs.append({k: job.get(k) for k in ("job_id", "label", "state", "created_at", "updated_at", "synthetic")})
            except (OSError, ValueError):
                continue
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

    def update(self, job_id: str, **fields) -> dict:
        with self.lock:
            job = self.get(job_id)
            job.update(fields, updated_at=now())
            write_json(self.directory(job_id) / "job.json", job)
            return job

    def create(self, source: Path, supplied: dict | None = None, label: str = "STEP 板卡", synthetic: bool = False) -> dict:
        job_id = uuid.uuid4().hex
        folder = self.root / job_id
        (folder / "input").mkdir(parents=True)
        target = folder / "input" / "board.step"
        shutil.copyfile(source, target)
        job = {"schema_version": "0.1", "job_id": job_id, "label": label[:120], "state": "inspecting",
               "created_at": now(), "updated_at": now(), "synthetic": synthetic, "events": [],
               "stage_results": {}, "artifacts": [], "issues": [], "review_url": f"/?job={job_id}"}
        write_json(folder / "job.json", job)
        try:
            proposal = inspect_step(target, job_id)
            if supplied:
                request = copy.deepcopy(supplied)
                request["job_id"] = job_id
                request.setdefault("board", {}).update(step_path=str(target), units="mm")
                request.setdefault("constraints", {})["confirmed"] = False
                proposal["request"] = request
            from .integration import attach_check_plan
            proposal["request"] = attach_check_plan(proposal["request"], proposal["inventory"])
            write_json(folder / "proposal.json", proposal)
            return self.update(job_id, state="awaiting_confirmation", proposal=proposal,
                               input_sha256=digest(target))
        except Exception as exc:
            return self.update(job_id, state="needs_input", issues=[{"code": "STEP_INSPECTION_FAILED", "severity": "error", "message": str(exc)}])

    def confirm(self, job_id: str, request: dict, wait: bool = False) -> dict:
        with self.lock:
            job = self.get(job_id)
            if job["state"] not in ("awaiting_confirmation", "needs_input", "failed"):
                raise ValueError("Job cannot be confirmed in its current state; create a new revision")
            if request.get("constraints", {}).get("confirmed") is not True:
                raise ValueError("constraints.confirmed must be explicitly true")
            from .review import validate_review
            validate_review(request, job["proposal"]["request"])
            target = self.directory(job_id) / "input" / "board.step"
            if digest(target) != job.get("input_sha256"):
                raise ValueError("Input STEP changed; re-import and review it")
            request = copy.deepcopy(request)
            request["job_id"] = job_id
            request["board"]["step_path"] = str(target)
            # Rebuild a derived plan only if it was not explicitly supplied by the caller.
            from .integration import attach_check_plan
            request = attach_check_plan(request, job["proposal"]["inventory"])
            write_json(self.directory(job_id) / "request.json", request)
            self.update(job_id, state="queued", request_sha256=digest(self.directory(job_id) / "request.json"))
        if wait:
            self.run(job_id)
        else:
            self.pool.submit(self.run, job_id)
        return self.get(job_id)

    def _stage(self, job_id: str, name: str, module: str, function: str, *args: Path, **kwargs) -> tuple[dict, Path]:
        self.update(job_id, state=name)
        output = self.directory(job_id) / name
        result_path = Path(getattr(importlib.import_module(module), function)(*args, output, **kwargs))
        result = read_json(result_path)
        if result.get("job_id") != job_id:
            raise ValueError(f"{name} returned a result for a different job")
        if name == "verifying":
            from .integration import supplemental_clearances, functional_access_checks
            if result.get("status") == "ok":
                extra = supplemental_clearances(read_json(args[0]), read_json(args[1]), args[1])
                extra.extend(functional_access_checks(read_json(args[0])))
                from .design_rules import fit_policy_checks
                extra.extend(fit_policy_checks(read_json(args[0]), read_json(args[1]), args[1]))
                if read_json(args[0]).get("constraints", {}).get("display"):
                    from .verification.display import display_checks
                    extra.extend(display_checks(read_json(args[0]), read_json(args[1]), args[1]))
                result["checks"].extend(extra)
                if any(x["status"] == "fail" and x.get("required", True) for x in extra):
                    result.update(status="failed", verdict="fail")
                elif any(x["status"] == "not_checked" and x.get("required", True) for x in extra):
                    result.update(status="needs_input", verdict="incomplete")
                result["check_summary"] = {status: sum(c["status"] == status for c in result["checks"])
                                           for status in ("pass", "fail", "not_checked", "not_applicable")}
                result["required_not_checked"] = [c["id"] for c in result["checks"]
                                                    if c.get("required") and c["status"] == "not_checked"]
            provenance = result.setdefault("inputs", {})
            provenance.setdefault("request_sha256", digest(args[0]))
            provenance.setdefault("cad_result_sha256", digest(args[1]))
            write_json(result_path, result)
        return self.register_stage_result(job_id, name, result_path)

    def register_stage_result(self, job_id: str, name: str, result_path: Path) -> tuple[dict, Path]:
        """Register hash-bound artifacts without rerunning CAD or changing state."""
        result_path = Path(result_path).resolve()
        folder = self.directory(job_id)
        if not result_path.is_relative_to(folder):
            raise ValueError("Stage result is outside its job directory")
        result = read_json(result_path)
        if result.get("job_id") != job_id:
            raise ValueError("Stage result belongs to another job")
        with self.lock:
            job = self.get(job_id)
            job["stage_results"][name] = {"path": str(result_path.relative_to(self.directory(job_id))), "result": result}
            job["artifacts"] = [a for a in job["artifacts"] if a.get("stage") != name]
            for index, item in enumerate(result.get("artifacts", [])):
                try:
                    path = resolve_artifact(result_path, item, self.directory(job_id))
                except (ValueError, OSError) as exc:
                    raise ValueError(f"{name} artifact invalid: {exc}") from exc
                job["artifacts"].append({**item, "path": str(path.relative_to(self.directory(job_id))),
                                         "url": f"/api/jobs/{job_id}/artifacts/{name}/{index}", "stage": name})
            job["issues"].extend(result.get("issues", []))
            job["events"].append({"at": now(), "stage": name, "status": result.get("status")})
            write_json(self.directory(job_id) / "job.json", job)
        return result, result_path

    def prepare_review_model(self, job_id: str, cad: dict, cad_path: Path) -> None:
        """Prepare a lossless display derivative; original geometry stays authoritative."""
        try:
            from .review_mesh import optimize_review_glb
            item = next((a for a in cad.get("artifacts", []) if a.get("kind") == "review_glb"), None)
            if item is None:
                return
            source = resolve_artifact(cad_path, item, self.directory(job_id))
            result_path = optimize_review_glb(source, self.directory(job_id)/"review_model",
                                              job_id=job_id)
            self.register_stage_result(job_id, "review_model", result_path)
        except Exception:
            job = self.get(job_id)
            self.update(job_id, issues=job["issues"] + [{"code": "REVIEW_OPTIMIZATION_UNAVAILABLE",
                        "severity": "warning", "message": "使用完整原始三维文件；显示优化不可用。"}])

    def run(self, job_id: str) -> None:
        folder = self.directory(job_id)
        request = folder / "request.json"
        try:
            if digest(request) != self.get(job_id)["request_sha256"]:
                raise ValueError("Confirmed request changed before execution")
            cad, cad_path = self._stage(job_id, "generating", "justbuild.cad", "generate", request)
            if cad.get("status") != "ok":
                self.update(job_id, state=cad.get("status", "failed"))
                return
            self.prepare_review_model(job_id, cad, cad_path)
            verification, verification_path = self._stage(job_id, "verifying", "justbuild.verification", "verify", request, cad_path)
            verdict = verification.get("verdict", "incomplete")
            if verdict == "fail":
                self.update(job_id, state="geometry_failed")
                return
            if verdict != "pass":
                self.update(job_id, state="review_incomplete")
                return
            prepared, _ = self._stage(job_id, "slicing", "justbuild.printing", "prepare_print", request, cad_path, verification_path,
                                     slicer=self.slicing.slicer)
            self.update(job_id, state="ready_to_print" if prepared.get("status") == "ok" else "review_ready")
        except Exception as exc:
            job = self.get(job_id)
            self.update(job_id, state="failed", issues=job["issues"] + [{"code": "PIPELINE_ERROR", "severity": "error", "message": str(exc)}])

    def artifact(self, job_id: str, stage: str, index: int) -> Path:
        folder = self.directory(job_id)
        info = self.get(job_id)["stage_results"][stage]
        result_path = folder / info["path"]
        return resolve_artifact(result_path, info["result"]["artifacts"][index], folder)

