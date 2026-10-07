"""Single-step tool jobs on files already on disk: auto-rig, Unity export, prop export.

The browser tabs upload what they work on; an agent (the MCP server, `mcp/`) already has
the files on this machine and wants one step run on them, through the same one-at-a-time
job queue the tabs use, so nothing fights a generation for memory. Each tool is a
script in `scripts/`; this only checks paths and builds its command.

    POST /api/tools/<tool>   JSON body of that tool's fields   -> {job_id, status_url}
    GET  /api/tools/<id>/status | events
    POST /api/tools/<id>/cancel

Paths must sit inside the repo or the data drive. A Unity project may be anywhere, but
must look like one (Assets/ and ProjectSettings/).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from image_to_3dlab import processes
from image_to_3dlab.autorig import RIG_CLASSES
from image_to_3dlab.data_root import data_root, inside
from image_to_3dlab.unity_export import safe_name, unity_project_problems

OUTPUT_ROOT = REPO / "output" / "tools"
SCRIPTS = REPO / "scripts"
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
TERMINAL = {"done", "error", "cancelled"}
TOOLS = ("autorig", "unity_export", "unity_export_props")


def allowed_roots() -> tuple[Path, ...]:
    root = data_root()
    return (REPO,) if root is None else (REPO, root)


def local_path(value: Any, *, kind: str = "file", field: str = "path") -> Path:
    """A path the caller named, checked: absolute or repo-relative, inside an allowed root,
    and existing. Normalised rather than resolved, so output/ (a symlink onto the data
    drive) still counts as the repo."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO / path
    path = Path(os.path.normpath(path))
    if not inside(path, allowed_roots()):
        raise ValueError(f"{field} must be inside {REPO} or the data drive")
    if kind == "file" and not path.is_file():
        raise ValueError(f"{field}: no such file {path}")
    if kind == "dir" and not path.is_dir():
        raise ValueError(f"{field}: no such folder {path}")
    return path


def unity_project(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    problems = unity_project_problems(path)
    if problems:
        raise ValueError("; ".join(problems))
    return path


def plan(tool: str, body: dict[str, Any], run_dir: Path) -> tuple[list[str], dict[str, Path]]:
    """(command, {name: output path}) for one tool call. Raises ValueError on bad input."""
    py = sys.executable
    if tool == "autorig":
        model = local_path(body.get("model"), field="model")
        rig_class = body.get("class", "humanoid")
        if rig_class not in RIG_CLASSES:
            raise ValueError(f"class must be one of {', '.join(RIG_CLASSES)}")
        out = run_dir / f"{safe_name(model.stem)}_rigged.glb"
        cmd = [py, str(SCRIPTS / "autorig.py"), str(model), str(out), "--class", rig_class,
               "--seed", str(int(body.get("seed", 0)))]
        if body.get("template_only"):
            cmd.append("--template-only")
        return cmd, {"rigged": out, "record": out.with_suffix(".autorig.json")}
    if tool == "unity_export":
        model = local_path(body.get("model"), field="model")
        name = safe_name(str(body.get("name") or model.stem))
        rig_class = body.get("class", "humanoid")
        if rig_class not in (*RIG_CLASSES, "none"):
            raise ValueError("class must be humanoid, quadruped, custom or none")
        out = run_dir / name
        cmd = [py, str(SCRIPTS / "unity_export.py"), str(model), str(out), "--name", name,
               "--class", rig_class, "--zip"]
        lods = [local_path(p, field="lods") for p in body.get("lods") or []]
        if lods:
            cmd += ["--lod", *map(str, lods)]
        if body.get("height") not in (None, ""):
            cmd += ["--height", str(float(body["height"]))]
        project = unity_project(body.get("unity_project"))
        if project:
            cmd += ["--unity-project", str(project)]
        return cmd, {"folder": out, "zip": out.with_suffix(".zip"),
                     "manifest": out / f"{name}.openmeshy.json"}
    if tool == "unity_export_props":
        finished = local_path(body.get("finished_dir"), kind="dir", field="finished_dir")
        out = run_dir / "unity"
        cmd = [py, str(SCRIPTS / "unity_export_props.py"), str(finished), str(out)]
        project = unity_project(body.get("unity_project"))
        if project:
            cmd += ["--unity-project", str(project)]
        return cmd, {"folder": out, "summary": out / "props.unity.json"}
    raise ValueError(f"unknown tool {tool!r}; one of {', '.join(TOOLS)}")


class ToolJob:
    def __init__(self, job_id: str, tool: str, directory: Path):
        self.id = job_id
        self.tool = tool
        self.directory = directory
        self.command: list[str] = []
        self.outputs: dict[str, Path] = {}
        self.status = "queued"
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.log_lines: deque[str] = deque(maxlen=300)

    def emit(self, event: dict[str, Any]) -> None:
        payload = {"elapsed_seconds": round(time.monotonic() - self.started, 1), **event}
        with self.condition:
            self.events.append(payload)
            self.condition.notify_all()


class ToolJobManager:
    def __init__(self, output_root: Path = OUTPUT_ROOT):
        self.output_root = output_root
        self.jobs: dict[str, ToolJob] = {}
        self.active: str | None = None
        self.lock = threading.Lock()

    def busy(self) -> bool:
        job = self.jobs.get(self.active) if self.active else None
        return job is not None and job.status not in TERMINAL

    def create(self, tool: str, body: dict[str, Any]) -> ToolJob:
        with self.lock:
            if self.busy():
                raise RuntimeError("another tool job is running")
            directory = self.output_root / f"{tool}__{time.strftime('%Y%m%d-%H%M%S')}__{uuid.uuid4().hex[:6]}"
            job = ToolJob(uuid.uuid4().hex, tool, directory)
            job.command, job.outputs = plan(tool, body, directory)
            directory.mkdir(parents=True)
            self.jobs[job.id] = job
            self.active = job.id
        return job

    def get(self, job_id: str) -> ToolJob | None:
        return self.jobs.get(job_id) if JOB_ID.match(job_id or "") else None

    def finish(self, job: ToolJob) -> None:
        with self.lock:
            if self.active == job.id:
                self.active = None


TOOL_JOBS = ToolJobManager()


def run_job(job: ToolJob, manager: ToolJobManager = TOOL_JOBS) -> None:
    try:
        job.status = "running"
        job.emit({"phase": "running", "message": f"{job.tool}: started"})
        job.process = subprocess.Popen(
            job.command, cwd=str(REPO), env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            **processes.group_popen_kwargs(),
        )
        assert job.process.stdout is not None
        for raw in job.process.stdout:
            line = raw.rstrip("\n")
            if line:
                job.log_lines.append(line)
                if line.startswith(("[autorig]", "[props]", "I2L_")):
                    job.emit({"phase": "running", "message": line[:300]})
        code = job.process.wait()
        if job.cancel_requested:
            job.status = "cancelled"
            job.emit({"phase": "error", "message": "Cancelled"})
        elif code != 0:
            job.status = "error"
            job.emit({"phase": "error", "message": f"{job.tool} exited with code {code}",
                      "log_tail": "\n".join(job.log_lines)[-6000:]})
        else:
            job.status = "done"
            job.emit({"phase": "done", "message": f"{job.tool}: done",
                      "outputs": {k: str(v) for k, v in job.outputs.items() if v.exists()}})
    except Exception as exc:  # reported to the caller, never swallowed
        job.status = "error"
        job.emit({"phase": "error", "message": str(exc)})
    finally:
        manager.finish(job)


def cancel_job(job: ToolJob) -> None:
    if job.status in TERMINAL:
        raise RuntimeError(f"job is already {job.status}")
    job.cancel_requested = True
    job.status = "cancelling"
    if job.process is not None and job.process.poll() is None:
        processes.terminate_group(job.process.pid)


def status_payload(job: ToolJob) -> dict[str, Any]:
    return {"status": job.status, "tool": job.tool, "directory": str(job.directory),
            "log_tail": "\n".join(job.log_lines)[-4000:],
            "last_event": job.events[-1] if job.events else None}
