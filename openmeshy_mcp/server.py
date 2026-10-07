"""OpenMeshy MCP server: image -> 3D -> rigged Unity asset, as tools an agent can call.

    .venv/bin/python -m openmeshy_mcp.server          # stdio, for Claude Code / Desktop
    claude mcp add openmeshy -- /path/to/repo/.venv/bin/python -m openmeshy_mcp.server

Every tool goes through the lab's HTTP API (viewer/serve.py, started on demand), so jobs
an agent starts queue with the browser's, show in its tabs, and never share the GPU.
Long jobs return a job id at once; `job_status(kind, job_id, wait_seconds)` waits for
them. Nothing here installs or downloads anything: weights are fetched only from the
lab's Setup & Status page, by a person who has seen their size and licence.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from openmeshy_mcp.client import (
    IMAGE_TYPES,
    KINDS,
    REPO,
    Lab,
    LabError,
    existing_file,
    resolve_path,
    stage_input,
    summarise,
)

INSTRUCTIONS = """\
Local image -> 3D -> Unity pipeline (Pixal3D, Blender, SkinTokens). All on this Mac.
Typical flow: image_to_unity(image) -> job_status('unity', id, wait_seconds=600) until done
-> render_preview(path) to look at it. One heavy job runs at a time; a 409 error means wait.
Generation takes ~12-15 min; finish ~30 s; autorig ~1 min; Unity export seconds.
Paths may be absolute or relative to the current project. Results land under the
OpenMeshy repo's output/; install_to_unity copies a finished Unity folder into a project.
Prop sheets (a grid of separate props in one image): generate_3d, then split_props with
names in reading order, then export_props_unity on the finished folder.
"""

server = MCPServer("openmeshy", instructions=INSTRUCTIONS)
lab = Lab()
MAX_WAIT = 900


def _started(kind: str, response: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
    job_id = response["job_id"]
    result: dict[str, Any] = {"kind": kind, "job_id": job_id}
    if wait_seconds > 0:
        result.update(summarise(lab.wait(kind, job_id, min(wait_seconds, MAX_WAIT))))
    else:
        result["next"] = f"job_status('{kind}', '{job_id}', wait_seconds=600)"
    return result


def _lab_file(value: str, suffixes: set[str], what: str) -> str:
    """A path the lab's tool jobs can read: resolved, checked, staged in if foreign."""
    from image_to_3dlab.data_root import data_root

    path = existing_file(value, suffixes, what)
    root = data_root()
    allowed = (REPO,) if root is None else (REPO, root)
    return str(stage_input(path, REPO / "output" / "mcp" / "inputs", allowed))


def _project(value: str) -> str:
    return str(resolve_path(value)) if value else ""


def _call(fn, *args, **kwargs) -> dict[str, Any]:
    """Run one lab call; turn its refusals into a plain error the agent can act on."""
    try:
        lab.ensure_lab()
        return fn(*args, **kwargs)
    except (LabError, ValueError) as exc:
        return {"error": str(exc)}


@server.tool()
def lab_status() -> dict[str, Any]:
    """What is installed and ready (generators, rigger, Blender) and what is running."""
    def go() -> dict[str, Any]:
        catalog = lab.get("/api/catalog")
        backends = [{"id": b.get("id"), "label": b.get("label"), "kind": b.get("kind"),
                     "state": b.get("state"), "license": b.get("license")}
                    for b in catalog.get("backends", []) if b.get("state") != "unsupported"]
        return {"lab": lab.base_url, "backends": backends,
                "note": "Install missing ones from the lab's Setup & Status page "
                        "(it shows size and licence first); this server never downloads."}
    return _call(go)


@server.tool()
def image_to_unity(image: str, rig_class: str = "humanoid", faces: int = 30000,
                   name: str = "", height_m: float | None = None, seed: int = 42,
                   wait_seconds: float = 0) -> dict[str, Any]:
    """One image -> textured, rigged, Unity-ready folder + zip (generate, finish, rig, export).

    rig_class: humanoid (Unity Humanoid, Mixamo names), quadruped, custom, or none (prop).
    faces: triangle target (10000 mobile, 30000 game, 60000 hero). height_m: real height
    (humanoids default to 1.8). Takes ~13-16 min; pass wait_seconds up to 900 to block.
    """
    def go() -> dict[str, Any]:
        path = existing_file(image, IMAGE_TYPES, "image")
        settings = {"class": rig_class, "faces": faces, "seed": seed, "name": name,
                    "height": height_m}
        response = lab.post_form("/api/unity", {"settings": json.dumps(settings)},
                                 {"image": path})
        return _started("unity", response, wait_seconds)
    return _call(go)


@server.tool()
def generate_3d(image: str, seed: int = 42, wait_seconds: float = 0) -> dict[str, Any]:
    """Image -> textured high-poly GLB with Pixal3D (MIT). ~12-15 min. For characters,
    single props or a whole prop sheet. When done, job_status reports result_url and the
    downloaded local path."""
    def go() -> dict[str, Any]:
        path = existing_file(image, IMAGE_TYPES, "image")
        settings = {"backend": "pixal3d", "seed": seed}
        response = lab.post_form("/api/generate", {"settings": json.dumps(settings)},
                                 {"image": path})
        return _started("generate", response, wait_seconds)
    return _call(go)


@server.tool()
def finish_model(model: str, image: str, faces: int = 30000,
                 wait_seconds: float = 120) -> dict[str, Any]:
    """Make a generated GLB game-ready: retopologise to `faces`, bake detail, put the
    source picture's real pixels back on (Pixel Match). ~30 s. `image` is the source art."""
    def go() -> dict[str, Any]:
        files = {"asset": existing_file(model, {".glb"}, "model"),
                 "image": existing_file(image, IMAGE_TYPES, "image")}
        response = lab.post_form("/api/finish", {"settings": json.dumps({"faces": faces})},
                                 files)
        return _started("finish", response, wait_seconds)
    return _call(go)


@server.tool()
def autorig(model: str, rig_class: str = "humanoid", seed: int = 0,
            wait_seconds: float = 180) -> dict[str, Any]:
    """Give a finished GLB a skeleton and skin weights: SkinTokens, else a template rig.
    rig_class: humanoid, quadruped or custom. Try another seed if the rig looks wrong."""
    return _call(lambda: _started("tool", lab.post_json(
        "/api/tools/autorig", {"model": _lab_file(model, {".glb"}, "model"),
                               "class": rig_class, "seed": seed}),
        wait_seconds))


@server.tool()
def export_unity(model: str, rig_class: str = "humanoid", name: str = "",
                 lods: list[str] | None = None, height_m: float | None = None,
                 unity_project: str = "", wait_seconds: float = 120) -> dict[str, Any]:
    """GLB -> Unity folder: FBX (1:1 scale, feet at origin, facing +Z), URP textures and
    a manifest the bundled OpenMeshyImporter.cs reads. `lods`: extra LOD GLBs (become a
    LODGroup). `unity_project`: copy straight into that project's Assets/OpenMeshy/."""
    def go() -> dict[str, Any]:
        body = {"model": _lab_file(model, {".glb"}, "model"), "class": rig_class,
                "name": name, "lods": [_lab_file(p, {".glb"}, "lod") for p in lods or []],
                "height": height_m, "unity_project": _project(unity_project)}
        return _started("tool", lab.post_json("/api/tools/unity_export", body), wait_seconds)
    return _call(go)


@server.tool()
def split_props(model: str, names: list[str], wait_seconds: float = 0) -> dict[str, Any]:
    """Split a generated prop-sheet GLB into separate, upright, named props with 3 LODs
    each. `names` in reading order (top row first, left to right), letters/digits/_/-."""
    def go() -> dict[str, Any]:
        files = {"asset": existing_file(model, {".glb"}, "model")}
        response = lab.post_form("/api/props", {"settings": json.dumps({"names": names})},
                                 files)
        return _started("props", response, wait_seconds)
    return _call(go)


@server.tool()
def export_props_unity(finished_dir: str, unity_project: str = "",
                       wait_seconds: float = 300) -> dict[str, Any]:
    """Export every finished prop (split_props output, `<prop>/<prop>_LOD<n>.glb`) to
    Unity: one FBX per prop with its LODs inside."""
    body = {"finished_dir": str(resolve_path(finished_dir)),
            "unity_project": _project(unity_project)}
    return _call(lambda: _started("tool", lab.post_json("/api/tools/unity_export_props", body),
                                  wait_seconds))


@server.tool()
def install_to_unity(unity_folder: str, unity_project: str = ".") -> dict[str, Any]:
    """Copy a finished Unity folder (the one holding NAME.fbx + NAME.openmeshy.json, from
    image_to_unity or export_unity) into a Unity project's Assets/OpenMeshy/, and install
    the OpenMeshyImporter.cs that sets it up on import. unity_project defaults to the
    current project."""
    from image_to_3dlab.unity_export import install_into_project

    try:
        folder = resolve_path(unity_folder)
        if not any(folder.glob("*.openmeshy.json")):
            return {"error": f"{folder} is not an OpenMeshy Unity folder (no *.openmeshy.json)"}
        dest = install_into_project(folder, resolve_path(unity_project))
        return {"installed": str(dest),
                "next": "Switch to Unity; it imports with the rig, scale and materials set."}
    except ValueError as exc:
        return {"error": str(exc)}


@server.tool()
def job_status(kind: str, job_id: str, wait_seconds: float = 0) -> dict[str, Any]:
    """State of a job: kind is generate, finish, props, unity or tool. With wait_seconds
    (max 900) it blocks until the job ends. Finished generate/finish jobs have their GLB
    saved locally; its path is in `local_path`."""
    if kind not in KINDS:
        return {"error": f"kind must be one of {', '.join(KINDS)}"}

    def go() -> dict[str, Any]:
        result = summarise(lab.wait(kind, job_id, min(wait_seconds, MAX_WAIT)))
        url = result.get("result_url")
        if result.get("status") == "done" and url:
            dest = REPO / "output" / "mcp" / f"{kind}_{job_id[:8]}" / "result.glb"
            if not dest.is_file():
                lab.download(url, dest)
            result["local_path"] = str(dest)
        return result
    return _call(go)


@server.tool()
def cancel_job(kind: str, job_id: str) -> dict[str, Any]:
    """Stop a running job (kind: generate, finish, props, unity, tool)."""
    if kind not in KINDS:
        return {"error": f"kind must be one of {', '.join(KINDS)}"}
    return _call(lambda: lab.cancel(kind, job_id))


@server.tool()
def render_preview(model: str, size: int = 512) -> Image | dict[str, Any]:
    """A front + side picture of a GLB, rendered by Blender in ~3 s, so you can see it."""
    try:
        path = existing_file(model, {".glb"}, "model")
    except ValueError as exc:
        return {"error": str(exc)}
    from image_to_3dlab.blender import find_blender

    blender = find_blender()
    if blender is None:
        return {"error": "Blender not found (set I2L_BLENDER)"}
    out = REPO / "output" / "mcp" / "previews" / f"{path.stem}_{int(time.time())}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [str(blender), "--background", "--python-exit-code", "1", "--python",
         str(REPO / "scripts" / "blender_preview.py"), "--", str(path), str(out),
         "--size", str(int(size))],
        capture_output=True, text=True, check=False, timeout=180)
    if process.returncode != 0 or not out.is_file():
        return {"error": "preview failed", "log_tail": (process.stdout + process.stderr)[-1500:]}
    return Image(path=out)


def recent_runs(root: Path, limit: int) -> list[dict[str, Any]]:
    """Newest result folders under output/, with the files an agent would want next."""
    folders = [p for sub in ("unity", "props", "tools", "finish", "mcp")
               for p in (root / sub).glob("*") if p.is_dir() and p.name != "previews"]
    folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    runs = []
    for folder in folders[:limit]:
        files = sorted(str(f) for f in folder.rglob("*")
                       if f.suffix in {".glb", ".fbx", ".zip"} and ".web." not in f.name)
        runs.append({"folder": str(folder), "kind": folder.parent.name, "files": files[:20]})
    return runs


@server.tool()
def list_outputs(limit: int = 10) -> list[dict[str, Any]]:
    """Recent results (Unity folders, props, rigs, finished models) and their files."""
    return recent_runs(REPO / "output", max(1, min(limit, 50)))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
