"""Automatic rigging: a skeleton and skin weights for a finished mesh, no hand fitting.

The learned route is SkinTokens (VAST-AI's TokenRig, MIT code and weights) through
`chris-straka/skintokens`, a Rust port that runs on Metal. It is run as a separate
program and never linked: the built binary is GPL-3.0 as a whole because of its shape
encoder, while the rigs it writes are model output and free to ship.

Humanoids come back with Mixamo names (``mixamorig:Hips``...), which Unity's Humanoid
avatar maps on its own. Quadrupeds and other creatures keep numbered bones and import
into Unity as Generic rigs.

When the learned rig fails the humanoid check, ``scripts/blender_autorig_template.py``
builds a template skeleton from the mesh's proportions and binds it with Blender's
automatic weights: cruder, but it always gives something to animate.

Everything here is pure (commands, checks, GLB reading) so it can be tested without
the model; ``scripts/autorig.py`` is the command-line tool that runs it.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKINTOKENS_DIR = REPO / "vendor" / "skintokens"
SKINTOKENS_UPSTREAM = "https://github.com/chris-straka/skintokens.git"
# The commit this repo was tested against (2026-10-07).
SKINTOKENS_COMMIT = "1e234cf"
SKINTOKENS_WEIGHTS_GB = 1.1

RIG_CLASSES = ("humanoid", "quadruped", "custom")
MIXAMO_PREFIX = "mixamorig:"

# What Unity's Humanoid avatar cannot do without, in Mixamo names. Chest, neck,
# shoulders, toes and fingers are optional there and so optional here.
HUMANOID_REQUIRED = (
    "Hips", "Spine", "Head",
    "LeftArm", "LeftForeArm", "LeftHand",
    "RightArm", "RightForeArm", "RightHand",
    "LeftUpLeg", "LeftLeg", "LeftFoot",
    "RightUpLeg", "RightLeg", "RightFoot",
)


def skintokens_binary(root: Path = SKINTOKENS_DIR) -> Path:
    return root / "target" / "release" / "skintokens"


def skintokens_home(root: Path = SKINTOKENS_DIR) -> Path:
    """Where the checkpoint lives: inside the checkout, so it follows vendor/ around."""
    return root / "home"


def skintokens_env(base: dict[str, str] | None = None, root: Path = SKINTOKENS_DIR) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.setdefault("SKINTOKENS_HOME", str(skintokens_home(root)))
    return env


def skintokens_ready(root: Path = SKINTOKENS_DIR) -> bool:
    return (skintokens_binary(root).is_file()
            and (skintokens_home(root) / "weights" / "grpo_1400.ckpt").is_file())


def rig_command(
    source: Path, output: Path, report: Path, rig_class: str = "humanoid",
    seed: int = 0, root: Path = SKINTOKENS_DIR,
) -> list[str]:
    """`skintokens rig`, as a list. The class must be one TokenRig knows."""
    if rig_class not in RIG_CLASSES:
        raise ValueError(f"rig class must be one of {', '.join(RIG_CLASSES)}, not {rig_class!r}")
    return [str(skintokens_binary(root)), "rig", str(source), str(output),
            "--report", str(report), "--class", rig_class, "--seed", str(seed)]


def bare(name: str) -> str:
    """A bone name without its Mixamo prefix."""
    return name.split(":", 1)[1] if name.startswith(MIXAMO_PREFIX) else name


@dataclass
class Skeleton:
    joints: list[str]
    parents: dict[str, str | None] = field(default_factory=dict)

    @property
    def names(self) -> set[str]:
        return {bare(j) for j in self.joints}


def read_glb_json(path: Path) -> dict:
    """The JSON chunk of a GLB: enough to see its skeleton without loading the mesh."""
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"{path} is not a GLB file")
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError(f"{path}: first chunk is not JSON")
    return json.loads(data[20:20 + length])


def glb_skeleton(path: Path) -> Skeleton | None:
    """The first skin's joints and their parents, or None for an unrigged GLB."""
    doc = read_glb_json(path)
    skins = doc.get("skins") or []
    if not skins:
        return None
    nodes = doc.get("nodes", [])
    names = [node.get("name", f"node_{i}") for i, node in enumerate(nodes)]
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            parent_of[child] = index
    joint_ids = skins[0].get("joints", [])
    joint_set = set(joint_ids)
    joints = [names[i] for i in joint_ids]
    parents = {
        names[i]: (names[parent_of[i]] if parent_of.get(i) in joint_set else None)
        for i in joint_ids
    }
    return Skeleton(joints, parents)


def humanoid_problems(skeleton: Skeleton | None) -> list[str]:
    """Why Unity's Humanoid avatar would reject this skeleton; empty when it would not."""
    if skeleton is None:
        return ["the GLB has no skin (nothing was rigged)"]
    missing = [name for name in HUMANOID_REQUIRED if name not in skeleton.names]
    problems = [f"missing bone {name}" for name in missing]
    roots = [j for j, p in skeleton.parents.items() if p is None]
    if len(roots) != 1:
        problems.append(f"expected one root bone, found {len(roots)}")
    return problems


def generic_problems(skeleton: Skeleton | None, minimum: int = 3) -> list[str]:
    """The bar for any creature: rigged, one root, enough bones to bend."""
    if skeleton is None:
        return ["the GLB has no skin (nothing was rigged)"]
    problems = []
    if len(skeleton.joints) < minimum:
        problems.append(f"only {len(skeleton.joints)} bones; need at least {minimum}")
    roots = [j for j, p in skeleton.parents.items() if p is None]
    if len(roots) != 1:
        problems.append(f"expected one root bone, found {len(roots)}")
    return problems


def problems_for(rig_class: str, skeleton: Skeleton | None) -> list[str]:
    return humanoid_problems(skeleton) if rig_class == "humanoid" else generic_problems(skeleton)


def unity_rig_type(rig_class: str, skeleton: Skeleton | None) -> str:
    """'Humanoid' only when Unity would accept it; everything else animates as Generic."""
    if skeleton is None:
        return "None"
    if rig_class == "humanoid" and not humanoid_problems(skeleton):
        return "Humanoid"
    return "Generic"


def read_report(path: Path) -> dict:
    """SkinTokens' JSON report, or a stand-in saying why there is none."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": f"no readable report: {exc}"}
