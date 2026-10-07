"""A small client for the lab's HTTP API, plus the pure helpers the MCP tools share.

The MCP server never runs a model itself. It asks the lab (viewer/serve.py), which owns
the one-job-at-a-time queue, so an agent's run and a person's run in the browser can
never both hold the GPU. If the lab is not running, `ensure_lab` starts it.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_URL = os.environ.get("OPENMESHY_LAB_URL", "http://127.0.0.1:8777")
# Job kinds and where their API lives. Each has /status and /cancel under /api/<path>/<id>.
KINDS = {"generate": "generate", "finish": "finish", "props": "props",
         "unity": "unity", "tool": "tools"}
TERMINAL = {"done", "error", "cancelled"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


class LabError(RuntimeError):
    """The lab refused or failed a request; the message is the lab's own words."""


def encode_multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    """A multipart/form-data body, as the lab's upload endpoints expect."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                      f"{value}\r\n").encode())
    for name, path in files.items():
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                      f"filename=\"{path.name}\"\r\nContent-Type: {kind}\r\n\r\n").encode()
                     + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def existing_file(value: str, suffixes: set[str] | None = None, what: str = "file") -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO / path
    if not path.is_file():
        raise ValueError(f"{what} not found: {path}")
    if suffixes and path.suffix.lower() not in suffixes:
        raise ValueError(f"{what} must be one of {', '.join(sorted(suffixes))}: {path}")
    return path


def summarise(status: dict[str, Any]) -> dict[str, Any]:
    """What an agent needs from a status payload: state, latest message, results."""
    event = status.get("last_event") or {}
    out = {"status": status.get("status"), "message": event.get("message"),
           "progress_pct": event.get("overall_pct")}
    for key in ("outputs", "rig", "folder", "timings_s", "run", "result_url", "zip_url",
                "preview_url", "log_tail", "directory"):
        if event.get(key) is not None:
            out[key] = event[key]
    if status.get("directory") and "directory" not in out:
        out["directory"] = status["directory"]
    if out["status"] == "error" and "log_tail" not in out and status.get("log_tail"):
        out["log_tail"] = status["log_tail"][-2000:]
    return out


class Lab:
    def __init__(self, base_url: str = DEFAULT_URL):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str | None = None, timeout: float = 60) -> Any:
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                kind = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")
            try:
                text = json.loads(text).get("error", text)
            except ValueError:
                pass
            raise LabError(f"{exc.code}: {text}") from exc
        return json.loads(data) if "json" in kind else data

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post_json(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, json.dumps(body).encode(), "application/json")

    def post_form(self, path: str, fields: dict[str, str], files: dict[str, Path]) -> Any:
        body, kind = encode_multipart(fields, files)
        return self._request("POST", path, body, kind, timeout=300)

    def alive(self) -> bool:
        try:
            self._request("GET", "/api/backends", timeout=3)
            return True
        except (OSError, LabError):
            return False

    def ensure_lab(self, wait: float = 60) -> None:
        """Start the lab in the background if it is not answering, and wait for it."""
        if self.alive():
            return
        python = REPO / ".venv" / "bin" / "python"
        log = REPO / "output" / "lab-mcp.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as handle:
            subprocess.Popen([str(python if python.exists() else sys.executable),
                              str(REPO / "viewer" / "serve.py"), "--no-browser"],
                             cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
                             start_new_session=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.alive():
                return
            time.sleep(1)
        raise LabError(f"the lab did not start; see {log}")

    def status(self, kind: str, job_id: str) -> dict[str, Any]:
        return self.get(f"/api/{KINDS[kind]}/{job_id}/status")

    def wait(self, kind: str, job_id: str, seconds: float, poll: float = 5) -> dict[str, Any]:
        """Poll until the job ends or `seconds` pass; return its last status."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            status = self.status(kind, job_id)
            if status.get("status") in TERMINAL or time.monotonic() >= deadline:
                return status
            time.sleep(min(poll, max(0.1, deadline - time.monotonic())))

    def cancel(self, kind: str, job_id: str) -> Any:
        return self.post_json(f"/api/{KINDS[kind]}/{job_id}/cancel", {})

    def download(self, url_path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._request("GET", url_path, timeout=300))
        return dest
