"""Image -> Unity jobs for the browser: one picture in, a rigged Unity-ready folder out.

A sibling of `finish_api.py` with the same job shape (one at a time, SSE progress,
artifacts by URL), driving `scripts/image_to_unity.py`, which does the work: Pixal3D,
Finish, auto-rig, Unity export. Each run is a folder under `output/unity/`:

    <name>__unity__YYYYMMDD-HHMMSS/
        input/source.png   input/settings.json
        steps/             each stage's GLB and log
        unity/<name>/      the folder to drag into Unity (and <name>.zip beside it)
        run.json           settings, timings, licence of every component
"""

from __future__ import annotations

import json
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
from image_to_3dlab.unity_export import safe_name

OUTPUT_ROOT = REPO / "output" / "unity"
WORKER = REPO / "scripts" / "image_to_unity.py"
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
TERMINAL = {"done", "error", "cancelled"}
STAGES = ("generate", "finish", "rig", "export")
STAGE_LABELS = {"generate": "Generate 3D (Pixal3D)", "finish": "Finish (retopology + bake)",
                "rig": "Auto-rig", "export": "Export for Unity"}
# Rough share of a run each stage takes on an M-series Mac, for the progress bar only.
STAGE_WEIGHT = {"generate": 0.6, "finish": 0.25, "rig": 0.1, "export": 0.05}
CLASSES = ("humanoid", "quadruped", "custom", "none")
DEFAULT_SETTINGS: dict[str, Any] = {"class": "humanoid", "faces": 30000, "seed": 42,
                                    "height": None, "name": ""}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def normalise_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Clamp everything that reaches a subprocess argument."""
    settings = dict(DEFAULT_SETTINGS)
    rig_class = raw.get("class", settings["class"])
    if rig_class not in CLASSES:
        raise ValueError(f"class must be one of {', '.join(CLASSES)}")
    settings["class"] = rig_class
    settings["faces"] = int(min(max(int(raw.get("faces", settings["faces"])), 2000), 200000))
    settings["seed"] = int(raw.get("seed", settings["seed"])) % (2 ** 31)
    height = raw.get("height")
    if height not in (None, ""):
        height = float(height)
        if not 0.05 <= height <= 50:
            raise ValueError("height must be between 0.05 and 50 metres")
        settings["height"] = height
    settings["name"] = safe_name(str(raw.get("name") or ""))[:60] if raw.get("name") else ""
    return settings


def build_command(job: UnityJob) -> list[str]:
    s = job.settings
    cmd = [sys.executable, str(WORKER), str(job.image_path), str(job.directory),
           "--name", job.name, "--class", s["class"], "--faces", str(s["faces"]),
           "--seed", str(s["seed"])]
    if s["height"] is not None:
        cmd += ["--height", str(s["height"])]
    if job.resume:
        cmd.append("--resume")
    return cmd


def parse_stage(line: str) -> tuple[str, str] | None:
    """`I2L_STAGE::rig::start` -> ("rig", "start")."""
    if not line.startswith("I2L_STAGE::"):
        return None
    parts = line.split("::", 2)
    if len(parts) != 3 or parts[1] not in STAGES:
        return None
    return parts[1], parts[2]


def overall_pct(stage: str, finished: bool) -> int:
    """Progress through the run at the start (or end) of a stage."""
    total = 0.0
    for name in STAGES:
        if name == stage:
            total += STAGE_WEIGHT[name] if finished else 0
            break
        total += STAGE_WEIGHT[name]
    return round(total * 100)


def served_url(path: Path) -> str | None:
    """URL under the repo-rooted static server; paths are kept unresolved so a symlinked
    output/ (the data drive) still counts as inside the repo."""
    try:
        return "/" + path.relative_to(REPO).as_posix()
    except ValueError:
        return None


class UnityJob:
    def __init__(self, job_id: str, directory: Path, name: str):
        self.id = job_id
        self.directory = directory
        self.name = name
        self.image_path = directory / "input" / "source.png"
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.status = "queued"
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.resume = False
        self.log_lines: deque[str] = deque(maxlen=300)

    @property
    def export_dir(self) -> Path:
        return self.directory / "unity" / self.name

    @property
    def zip_path(self) -> Path:
        return self.export_dir.with_suffix(".zip")

    @property
    def preview_glb(self) -> Path:
        steps = self.directory / "steps"
        rigged = steps / "3_rigged.glb"
        return rigged if rigged.is_file() else steps / "2_finished.glb"

    @property
    def run_record(self) -> Path:
        return self.directory / "run.json"

    def emit(self, event: dict[str, Any]) -> None:
        payload = {"elapsed_seconds": round(time.monotonic() - self.started, 1), **event}
        with self.condition:
            self.events.append(payload)
            self.condition.notify_all()


class UnityJobManager:
    def __init__(self, output_root: Path = OUTPUT_ROOT):
        self.output_root = output_root
        self.jobs: dict[str, UnityJob] = {}
        self.active: str | None = None
        self.lock = threading.Lock()

    def busy(self) -> bool:
        job = self.jobs.get(self.active) if self.active else None
        return job is not None and job.status not in TERMINAL

    def create(self, image_name: str, image: bytes, raw_settings: dict[str, Any]) -> UnityJob:
        if Path(image_name).suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("the image must be PNG, JPG or WebP")
        settings = normalise_settings(raw_settings)
        name = settings["name"] or safe_name(Path(image_name).stem)[:60]
        with self.lock:
            if self.busy():
                raise RuntimeError("an Image -> Unity run is already going")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            directory = self.output_root / f"{name}__unity__{stamp}"
            suffix = 1
            while directory.exists():
                suffix += 1
                directory = self.output_root / f"{name}__unity__{stamp}-{suffix}"
            job = UnityJob(uuid.uuid4().hex, directory, name)
            job.settings = settings
            (directory / "input").mkdir(parents=True)
            _write_png(image, job.image_path)
            (directory / "input" / "settings.json").write_text(json.dumps(settings, indent=2))
            self.jobs[job.id] = job
            self.active = job.id
        return job

    def get(self, job_id: str) -> UnityJob | None:
        return self.jobs.get(job_id) if JOB_ID.match(job_id or "") else None

    def finish(self, job: UnityJob) -> None:
        with self.lock:
            if self.active == job.id:
                self.active = None


UNITY_JOBS = UnityJobManager()
ARTIFACTS = {
    "preview.glb": ("preview_glb", "model/gltf-binary", "inline"),
    "unity.zip": ("zip_path", "application/zip", "attachment"),
    "run.json": ("run_record", "application/json", "inline"),
}


def _write_png(data: bytes, path: Path) -> None:
    """Store the upload as PNG whatever it came as, so every stage reads one format."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image.save(path, "PNG")


def run_job(job: UnityJob, manager: UnityJobManager = UNITY_JOBS) -> None:
    try:
        job.status = "running"
        job.emit({"phase": "queued", "overall_pct": 0, "stages": list(STAGES),
                  "stage_labels": STAGE_LABELS, "message": "Starting"})
        job.process = subprocess.Popen(
            build_command(job), cwd=str(REPO), env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            **processes.group_popen_kwargs(),
        )
        assert job.process.stdout is not None
        for raw in job.process.stdout:
            line = raw.rstrip("\n")
            if not line:
                continue
            job.log_lines.append(line)
            stage = parse_stage(line)
            if stage:
                name, message = stage
                finished = message.startswith(("done", "reused", "skipped"))
                event = {"phase": name, "overall_pct": overall_pct(name, finished),
                         "message": f"{STAGE_LABELS[name]}: {message}"}
                if finished:
                    event["stage_pct"] = 100
                job.emit(event)
        code = job.process.wait()
        if job.cancel_requested:
            job.status = "cancelled"
            job.emit({"phase": "error", "message": "Cancelled"})
        elif code != 0:
            job.status = "error"
            job.emit({"phase": "error", "message": f"pipeline exited with code {code}",
                      "log_tail": "\n".join(job.log_lines)[-6000:]})
        else:
            job.status = "done"
            record = json.loads(job.run_record.read_text()) if job.run_record.is_file() else {}
            job.emit({
                "phase": "done", "overall_pct": 100, "message": "Ready for Unity",
                "preview_url": f"/api/unity/{job.id}/preview.glb",
                "zip_url": f"/api/unity/{job.id}/unity.zip",
                "run_url": f"/api/unity/{job.id}/run.json",
                "folder": str(job.export_dir),
                "rig": record.get("rig"),
                "timings_s": record.get("timings_s"),
            })
    except Exception as exc:  # reported to the browser, never swallowed
        job.status = "error"
        job.emit({"phase": "error", "message": str(exc)})
    finally:
        manager.finish(job)


def cancel_job(job: UnityJob) -> None:
    if job.status in TERMINAL:
        raise RuntimeError(f"job is already {job.status}")
    job.cancel_requested = True
    job.status = "cancelling"
    if job.process is not None and job.process.poll() is None:
        processes.terminate_group(job.process.pid)


def status_payload(job: UnityJob) -> dict[str, Any]:
    return {"status": job.status, "settings": job.settings, "name": job.name,
            "directory": job.directory.name, "log_tail": "\n".join(job.log_lines)[-4000:],
            "last_event": job.events[-1] if job.events else None}
