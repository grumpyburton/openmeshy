#!/usr/bin/env python3
"""Rig a character or creature with a template skeleton fitted to its bounding box.

    blender --background --python-exit-code 1 --python scripts/blender_autorig_template.py -- \
        IN.glb OUT.glb --class humanoid|quadruped

The fallback for `scripts/autorig.py` when the learned rigger fails. It assumes the model
stands upright, facing glTF's front (Blender's -Y), arms out or down; it places bones
by proportion, not by looking, then binds them with Blender's automatic (heat) weights.
Humanoid bones carry Mixamo names, so Unity's Humanoid avatar maps them unaided.

Prints `I2L_AUTORIG_TEMPLATE {json}` with the bone count and how many vertices ended up
with no weight at all.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

# Shared Blender helpers (blender_rebind_weights) live beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Box:
    """Axis-aligned bounds in Blender space (Z up, the model facing -Y)."""

    min: Vec
    max: Vec

    @property
    def size(self) -> Vec:
        return tuple(b - a for a, b in zip(self.min, self.max))  # type: ignore[return-value]

    @property
    def centre(self) -> Vec:
        return tuple((a + b) / 2 for a, b in zip(self.min, self.max))  # type: ignore[return-value]


@dataclass(frozen=True)
class Joint:
    name: str
    head: Vec
    tail: Vec
    parent: str | None


def _mirror(joints: list[Joint]) -> list[Joint]:
    """Right-side copies of every Left* joint (character's left is +X)."""
    out = []
    for j in joints:
        if not j.name.startswith("mixamorig:Left") and not j.name.endswith(".L"):
            continue
        name = j.name.replace("Left", "Right") if "Left" in j.name else j.name[:-2] + ".R"
        parent = j.parent
        if parent and ("Left" in parent or parent.endswith(".L")):
            parent = parent.replace("Left", "Right") if "Left" in parent else parent[:-2] + ".R"
        out.append(Joint(name, (-j.head[0], j.head[1], j.head[2]),
                         (-j.tail[0], j.tail[1], j.tail[2]), parent))
    return out


def humanoid_joints(box: Box) -> list[Joint]:
    """A 22-bone Mixamo skeleton scaled to the box. X is centred on the box, so the
    skeleton is symmetric even when an arm pose makes the mesh slightly lopsided."""
    cx, cy, z0 = box.centre[0], box.centre[1], box.min[2]
    h = box.size[2]
    half_w = box.size[0] / 2

    def p(x: float, z: float, y: float = 0.0) -> Vec:
        return (cx + x * h, cy + y * h, z0 + z * h)

    m = "mixamorig:"
    spine = [
        Joint(m + "Hips", p(0, 0.52), p(0, 0.58), None),
        Joint(m + "Spine", p(0, 0.58), p(0, 0.65), m + "Hips"),
        Joint(m + "Spine1", p(0, 0.65), p(0, 0.72), m + "Spine"),
        Joint(m + "Spine2", p(0, 0.72), p(0, 0.80), m + "Spine1"),
        Joint(m + "Neck", p(0, 0.82), p(0, 0.87), m + "Spine2"),
        Joint(m + "Head", p(0, 0.87), p(0, 1.0), m + "Neck"),
    ]
    # Arms: from the shoulder to wherever the hand must be for the mesh to be this wide.
    # Wide mesh -> T-pose, horizontal; narrow -> arms hang down at an angle.
    shoulder_z, shoulder_x = 0.81, 0.09
    arm_len = 0.38
    reach = max(half_w / h - shoulder_x, 0.05)
    reach = min(reach, arm_len)
    drop = math.sqrt(max(arm_len ** 2 - reach ** 2, 0.0))
    hand_end = (shoulder_x + reach, shoulder_z - drop)

    def along(t: float) -> tuple[float, float]:
        return (shoulder_x + (hand_end[0] - shoulder_x) * t,
                shoulder_z + (hand_end[1] - shoulder_z) * t)

    elbow, wrist = along(0.47), along(0.86)
    left = [
        Joint(m + "LeftShoulder", p(0.03, shoulder_z), p(shoulder_x, shoulder_z), m + "Spine2"),
        Joint(m + "LeftArm", p(shoulder_x, shoulder_z), p(*elbow), m + "LeftShoulder"),
        Joint(m + "LeftForeArm", p(*elbow), p(*wrist), m + "LeftArm"),
        Joint(m + "LeftHand", p(*wrist), p(*hand_end), m + "LeftForeArm"),
        Joint(m + "LeftUpLeg", p(0.09, 0.50), p(0.09, 0.27), m + "Hips"),
        Joint(m + "LeftLeg", p(0.09, 0.27), p(0.09, 0.05), m + "LeftUpLeg"),
        Joint(m + "LeftFoot", p(0.09, 0.05), p(0.09, 0.015, -0.07), m + "LeftLeg"),
        Joint(m + "LeftToeBase", p(0.09, 0.015, -0.07), p(0.09, 0.01, -0.13), m + "LeftFoot"),
    ]
    return spine + left + _mirror(left)


def quadruped_joints(box: Box) -> list[Joint]:
    """A four-legged skeleton: spine along -Y (head at the front), legs to the floor."""
    cx, cy, z0 = box.centre[0], box.centre[1], box.min[2]
    sx, sy, h = box.size

    def p(x: float, y: float, z: float) -> Vec:
        # x as a fraction of width, y of length (negative = front), z of height.
        return (cx + x * sx, cy + y * sy, z0 + z * h)

    spine = [
        Joint("Hips", p(0, 0.30, 0.62), p(0, 0.12, 0.64), None),
        Joint("Spine", p(0, 0.12, 0.64), p(0, -0.08, 0.66), "Hips"),
        Joint("Chest", p(0, -0.08, 0.66), p(0, -0.26, 0.68), "Spine"),
        Joint("Neck", p(0, -0.26, 0.68), p(0, -0.38, 0.82), "Chest"),
        Joint("Head", p(0, -0.38, 0.82), p(0, -0.50, 0.85), "Neck"),
        Joint("Tail", p(0, 0.32, 0.62), p(0, 0.50, 0.55), "Hips"),
    ]
    left = [
        Joint("FrontUpperLeg.L", p(0.22, -0.24, 0.62), p(0.22, -0.24, 0.34), "Chest"),
        Joint("FrontLowerLeg.L", p(0.22, -0.24, 0.34), p(0.22, -0.24, 0.06), "FrontUpperLeg.L"),
        Joint("FrontFoot.L", p(0.22, -0.24, 0.06), p(0.22, -0.30, 0.0), "FrontLowerLeg.L"),
        Joint("BackUpperLeg.L", p(0.22, 0.28, 0.60), p(0.22, 0.30, 0.32), "Hips"),
        Joint("BackLowerLeg.L", p(0.22, 0.30, 0.32), p(0.22, 0.28, 0.06), "BackUpperLeg.L"),
        Joint("BackFoot.L", p(0.22, 0.28, 0.06), p(0.22, 0.22, 0.0), "BackLowerLeg.L"),
    ]
    return spine + left + _mirror(left)


def template_joints(rig_class: str, box: Box) -> list[Joint]:
    if rig_class == "humanoid":
        return humanoid_joints(box)
    if rig_class == "quadruped":
        return quadruped_joints(box)
    raise ValueError(f"no template for rig class {rig_class!r} (humanoid or quadruped)")


# Fraction of vertices allowed to end up with no weight at all.
VOXEL_FALLBACK_AT = 0.02
MAX_UNWEIGHTED = 0.05


def needs_voxel_fallback(unweighted: int, total: int, limit: float = VOXEL_FALLBACK_AT) -> bool:
    return total > 0 and unweighted / total > limit


def unweighted_count(obj) -> int:
    return sum(1 for v in obj.data.vertices if not any(g.weight > 0 for g in v.groups))


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    import argparse

    parser = argparse.ArgumentParser(prog="blender_autorig_template.py")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--class", dest="rig_class", default="humanoid",
                        choices=("humanoid", "quadruped"))
    args = parser.parse_args(argv)
    return args.source, args.output, args.rig_class


def main() -> None:
    import bpy  # only inside Blender

    source, output, rig_class = parse_args(sys.argv[sys.argv.index("--") + 1:])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"{source} has no mesh")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    corners = [obj.matrix_world @ v.co for obj in meshes for v in obj.data.vertices]
    box = Box(tuple(min(c[i] for c in corners) for i in range(3)),
              tuple(max(c[i] for c in corners) for i in range(3)))
    joints = template_joints(rig_class, box)

    arm_data = bpy.data.armatures.new("Armature")
    rig = bpy.data.objects.new("Armature", arm_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bones = {}
    for joint in joints:
        bone = arm_data.edit_bones.new(joint.name)
        bone.head, bone.tail = joint.head, joint.tail
        bones[joint.name] = bone
    for joint in joints:
        if joint.parent:
            bones[joint.name].parent = bones[joint.parent]
            bones[joint.name].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")

    # Heat weighting gives up silently on overlapping or leaky meshes, which generated
    # ones often are. Then weight a watertight voxel copy and transfer the result.
    transfers = []
    for obj in meshes:
        if needs_voxel_fallback(unweighted_count(obj), len(obj.data.vertices)):
            from blender_rebind_weights import transfer_weights

            transfers.append(transfer_weights(bpy, obj, rig))
    unweighted = sum(unweighted_count(obj) for obj in meshes)
    total = sum(len(o.data.vertices) for o in meshes)
    ok = not needs_voxel_fallback(unweighted, total, MAX_UNWEIGHTED)
    bpy.ops.export_scene.gltf(filepath=output, export_format="GLB", export_skins=True,
                              export_animations=False)
    print("I2L_AUTORIG_TEMPLATE " + json.dumps({
        "ok": ok, "class": rig_class, "bones": len(joints), "unweighted_vertices": unweighted,
        "vertices": total, "voxel_transfers": transfers,
    }), flush=True)
    if not ok:
        raise SystemExit(f"{unweighted} of {total} vertices have no weight")


if __name__ == "__main__":
    main()
