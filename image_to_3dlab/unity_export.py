"""Unity-ready output: the parts of the export that are plain maths and file writing.

Unity reads FBX for skinned characters (its Humanoid avatar is built from an FBX), and
its URP Lit shader wants metal and gloss in one texture: metallic in red, smoothness in
alpha. glTF stores them the other way (roughness in green, metallic in blue), so the
texture is repacked here. A small JSON manifest beside the FBX tells
`unity/OpenMeshy/Editor/OpenMeshyImporter.cs` the rig type and which texture is which,
so the model arrives in Unity already set up instead of needing a dozen clicks.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMPORTER = REPO / "unity" / "OpenMeshy" / "Editor" / "OpenMeshyImporter.cs"
MANIFEST_SUFFIX = ".openmeshy.json"
MANIFEST_VERSION = 1

# Default real-world height, in metres, when a class implies one. glTF is in metres and
# generators emit roughly unit-sized models, so a person would otherwise be 1 m tall.
DEFAULT_HEIGHT = {"humanoid": 1.8}

TEXTURE_ROLES = ("baseColor", "normal", "metallicSmoothness", "occlusion", "emission")


def safe_name(text: str) -> str:
    """A file-name-safe version of a material or asset name."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return cleaned or "asset"


def texture_file(material: str, role: str) -> str:
    if role not in TEXTURE_ROLES:
        raise ValueError(f"unknown texture role {role!r}")
    return f"{safe_name(material)}_{role}.png"


def scale_for_height(current: float, rig_class: str, height: float | None = None) -> float:
    """Uniform scale that makes the model ``height`` metres tall (or its class default).

    1.0 when no height applies, so props and creatures keep the size they came with.
    """
    target = height if height is not None else DEFAULT_HEIGHT.get(rig_class)
    if not target or current <= 0:
        return 1.0
    return target / current


def pack_metallic_smoothness(metal_rough):
    """glTF metallicRoughness (G = roughness, B = metallic) -> URP's MetallicGlossMap
    (R = metallic, A = smoothness = 1 - roughness). Takes and returns a PIL image."""
    from PIL import Image, ImageChops

    rgb = metal_rough.convert("RGB")
    _, rough, metal = rgb.split()
    smooth = ImageChops.invert(rough)
    black = Image.new("L", rgb.size, 0)
    return Image.merge("RGBA", (metal, black, black, smooth))


def build_manifest(name: str, rig: str, materials: list[dict], has_animations: bool,
                   scale: float, source: str, autorig: dict | None = None) -> dict:
    """What the Unity importer reads. Kept flat: Unity's JsonUtility cannot read maps."""
    if rig not in ("Humanoid", "Generic", "None"):
        raise ValueError(f"rig must be Humanoid, Generic or None, not {rig!r}")
    return {
        "version": MANIFEST_VERSION,
        "name": name,
        "rig": rig,
        "hasAnimations": has_animations,
        "appliedScale": scale,
        "source": source,
        "materials": [
            {
                "name": m["name"],
                "baseColorFactor": list(m.get("baseColorFactor", [1, 1, 1, 1])),
                "metallic": float(m.get("metallic", 0.0)),
                "smoothness": float(m.get("smoothness", 0.5)),
                **{role: m.get(role, "") for role in TEXTURE_ROLES},
            }
            for m in materials
        ],
        "autorig": {
            "route": (autorig or {}).get("route"),
            "components": (autorig or {}).get("components", []),
        },
    }


def write_manifest(path: Path, manifest: dict) -> Path:
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def unity_project_problems(project: Path) -> list[str]:
    """Why ``project`` does not look like a Unity project root."""
    problems = []
    for needed in ("Assets", "ProjectSettings"):
        if not (project / needed).is_dir():
            problems.append(f"{project} has no {needed}/ folder")
    return problems


def install_into_project(export_dir: Path, project: Path, importer: Path = IMPORTER) -> Path:
    """Copy an export into ``Assets/OpenMeshy/<name>/`` and the importer beside it (once).

    The importer goes in its own Editor folder, never inside each export: two copies of
    one C# class in a project is a compile error.
    """
    problems = unity_project_problems(project)
    if problems:
        raise ValueError("; ".join(problems))
    editor = project / "Assets" / "OpenMeshy" / "Editor"
    editor.mkdir(parents=True, exist_ok=True)
    shutil.copy2(importer, editor / importer.name)
    dest = project / "Assets" / "OpenMeshy" / export_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(export_dir, dest)
    return dest
