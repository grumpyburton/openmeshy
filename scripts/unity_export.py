#!/usr/bin/env python3
"""Turn a GLB (rigged or not) into a folder Unity imports ready to use: FBX, textures, manifest.

    python scripts/unity_export.py rigged.glb out/unity/robot --class humanoid
    python scripts/unity_export.py rigged.glb out/unity/robot --unity-project ~/MyGame
    python scripts/unity_export.py prop.glb out/unity/crate --class none --zip

Writes `NAME.fbx`, `textures/` (base colour, normal, metallic-smoothness packed for URP,
occlusion, emission) and `NAME.openmeshy.json`. With `--unity-project` it also copies the
folder to `Assets/OpenMeshy/NAME/` and installs `OpenMeshyImporter.cs`, which sets the
rig (Humanoid when the skeleton qualifies, else Generic) and builds URP Lit materials.
Humanoids are scaled to 1.8 m unless `--height` says otherwise.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import autorig, unity_export
from image_to_3dlab.blender import find_blender

SCRIPTS = REPO / "scripts"


def export_command(blender: Path, source: Path, out_dir: Path, name: str, height: float) -> list[str]:
    """Blender measures and scales the model itself: only it sees the scene the FBX holds."""
    return [str(blender), "--background", "--python-exit-code", "1",
            "--python", str(SCRIPTS / "blender_unity_export.py"), "--",
            str(source), str(out_dir), name, f"{height:.4f}"]


def target_height(rig_class: str, height: float | None) -> float:
    """Metres to scale to, or 0 to keep the model's own size."""
    return height if height is not None else unity_export.DEFAULT_HEIGHT.get(rig_class, 0.0)


def parse_marker(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("I2L_UNITY_EXPORT "):
            return json.loads(line[len("I2L_UNITY_EXPORT "):])
    raise RuntimeError("Blender export printed no I2L_UNITY_EXPORT line")


def finish_textures(out_dir: Path, materials: list[dict]) -> list[dict]:
    """Repack each raw metallic-roughness PNG into URP's metallic-smoothness map."""
    from PIL import Image

    textures = out_dir / "textures"
    for mat in materials:
        raw = mat.pop("metalRough", None)
        if not raw:
            continue
        name = unity_export.texture_file(mat["name"], "metallicSmoothness")
        with Image.open(textures / raw) as image:
            unity_export.pack_metallic_smoothness(image).save(textures / name)
        (textures / raw).unlink()
        mat["metallicSmoothness"] = name
        # With a map, the constants become multipliers in URP: keep them neutral.
        mat["metallic"], mat["smoothness"] = 1.0, 1.0
    return materials


def export(source: Path, out_dir: Path, name: str, rig_class: str = "humanoid",
           height: float | None = None, autorig_record: dict | None = None) -> dict:
    blender = find_blender()
    if blender is None:
        raise SystemExit("Blender not found. Install Blender 4.2+ or set I2L_BLENDER.")
    out_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(export_command(blender, source, out_dir, name,
                                            target_height(rig_class, height)),
                             capture_output=True, text=True, check=False)
    if process.returncode != 0:
        tail = "\n".join((process.stdout + process.stderr).splitlines()[-20:])
        raise SystemExit(f"Blender export failed (exit {process.returncode}):\n{tail}")
    result = parse_marker(process.stdout)
    materials = finish_textures(out_dir, result["materials"])
    skeleton = autorig.glb_skeleton(source)
    rig = autorig.unity_rig_type(rig_class if rig_class != "none" else "custom", skeleton)
    manifest = unity_export.build_manifest(
        unity_export.safe_name(name), rig, materials, result["has_animations"], result["scale"],
        str(source), autorig_record)
    path = out_dir / f"{unity_export.safe_name(name)}{unity_export.MANIFEST_SUFFIX}"
    unity_export.write_manifest(path, manifest)
    return {"manifest": str(path), "fbx": result["fbx"], "rig": rig, "scale": result["scale"],
            "height_m": round(result["height"], 3), "bones": result["bones"],
            "unweighted_vertices": result["unweighted_vertices"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--name", help="asset name (default: the output folder's name)")
    parser.add_argument("--class", dest="rig_class", default="humanoid",
                        choices=(*autorig.RIG_CLASSES, "none"))
    parser.add_argument("--height", type=float, help="target height in metres")
    parser.add_argument("--autorig-record", type=Path,
                        help="the .autorig.json from scripts/autorig.py (default: beside SOURCE)")
    parser.add_argument("--unity-project", type=Path, help="copy into this Unity project")
    parser.add_argument("--zip", action="store_true", help="also write OUT_DIR.zip")
    args = parser.parse_args(argv)

    record_path = args.autorig_record or args.source.with_suffix(".autorig.json")
    record = json.loads(record_path.read_text()) if record_path.is_file() else None
    name = args.name or args.out_dir.name
    summary = export(args.source, args.out_dir, name, args.rig_class, args.height, record)
    if args.zip:
        summary["zip"] = shutil.make_archive(str(args.out_dir), "zip", args.out_dir.parent,
                                             args.out_dir.name)
    if args.unity_project:
        summary["installed"] = str(unity_export.install_into_project(
            args.out_dir, args.unity_project.expanduser()))
    print("I2L_UNITY " + json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
