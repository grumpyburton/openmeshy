"""Tiny hand-built GLBs for tests: only the JSON chunk matters to the code under test."""

from __future__ import annotations

import json
import struct
from pathlib import Path


def write_glb(path: Path, doc: dict) -> Path:
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    header = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(body))
    path.write_bytes(header + struct.pack("<I4s", len(body), b"JSON") + body)
    return path


def skinned_doc(names: list[str], parents: dict[str, str | None]) -> dict:
    """Nodes named `names`, linked by `parents`, all joints of one skin, plus a mesh node."""
    index = {n: i for i, n in enumerate(names)}
    nodes = [{"name": n} for n in names]
    for child, parent in parents.items():
        if parent is not None:
            nodes[index[parent]].setdefault("children", []).append(index[child])
    nodes.append({"name": "Body", "mesh": 0, "skin": 0})
    return {"asset": {"version": "2.0"}, "nodes": nodes,
            "skins": [{"joints": list(range(len(names)))}]}


MIXAMO_PARENTS = {
    "Hips": None, "Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2",
    "Head": "Neck",
    "LeftShoulder": "Spine2", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm",
    "LeftHand": "LeftForeArm",
    "RightShoulder": "Spine2", "RightArm": "RightShoulder", "RightForeArm": "RightArm",
    "RightHand": "RightForeArm",
    "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg",
    "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg",
}


def mixamo_doc(prefix: str = "mixamorig:", drop: tuple[str, ...] = ()) -> dict:
    parents = {prefix + k: (prefix + v if v else None)
               for k, v in MIXAMO_PARENTS.items() if k not in drop}
    parents = {k: (v if v is None or v in parents else None) for k, v in parents.items()}
    return skinned_doc(list(parents), parents)
