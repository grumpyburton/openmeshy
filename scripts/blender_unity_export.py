#!/usr/bin/env python3
"""Export a (rigged) GLB as a Unity-ready FBX, with each material's textures as PNGs.

    blender --background --python-exit-code 1 --python scripts/blender_unity_export.py -- \
        IN.glb OUT_DIR NAME HEIGHT [--lod LOD1.glb LOD2.glb ...]

Scales the model uniformly to HEIGHT metres (0 keeps its size) and applies it, so Unity
imports it at 1:1 with no stray transform. Bone display shapes the glTF importer adds
are dropped, so they neither count towards the height nor ship as meshes. Writes `OUT_DIR/NAME.fbx` with Unity's axes (Y up, -Z forward, no leaf
bones, deform bones only) and `OUT_DIR/textures/<material>_<role>.png`. The metallic-
roughness texture is written raw (`_metalRough.png`); `scripts/unity_export.py` repacks it.
With `--lod`, IN is LOD0 and each further GLB the next level of detail: all go into the
one FBX as meshes named `NAME_LOD0`, `NAME_LOD1`..., which Unity turns into a LODGroup
on import with no setup. Each LOD keeps its own material and textures.
Prints `I2L_UNITY_EXPORT {json}` describing materials, rig and animations.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def fbx_kwargs(path: str, has_animations: bool) -> dict:
    """Blender's FBX exporter settings for Unity.

    Axis conversion is left to Unity's "Bake Axis Conversion" (the importer turns it on)
    rather than Blender's experimental apply-transform, which bends skinned meshes.
    """
    return {
        "filepath": path,
        "use_selection": False,
        "object_types": {"ARMATURE", "MESH"},
        "apply_unit_scale": True,
        "apply_scale_options": "FBX_SCALE_UNITS",
        "axis_forward": "-Z",
        "axis_up": "Y",
        "use_space_transform": True,
        "bake_space_transform": False,
        "add_leaf_bones": False,
        "primary_bone_axis": "Y",
        "secondary_bone_axis": "X",
        "use_armature_deform_only": True,
        "armature_nodetype": "NULL",
        "mesh_smooth_type": "FACE",
        "use_tspace": True,
        "path_mode": "STRIP",
        "embed_textures": False,
        "bake_anim": has_animations,
        "bake_anim_use_all_actions": has_animations,
        "bake_anim_use_nla_strips": False,
    }


# Degrees about Blender's Z applied before export. Generated models face glTF's front
# (Blender -Y); with the axis settings below that arrived in Unity facing -Z, checked by
# importing into Unity 6 (2026-10-07). Unity characters face +Z, hence the half turn.
UNITY_FACING_TURN = 180.0


def mesh_bounds(meshes) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """World-space (min, max) corners of the meshes' vertices."""
    points = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    if not points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def ground_offset(lo, hi) -> tuple[float, float, float]:
    """Translation putting the model's feet on the origin: centred in X and Y, lowest
    point at Z = 0. Unity characters pivot at their feet; a centred pivot sinks them
    halfway into the floor."""
    return (-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])


def mesh_height(meshes) -> float:
    """World-space height (Blender Z) of the meshes' vertices."""
    lo, hi = mesh_bounds(meshes)
    return hi[2] - lo[2]


def parse_args(argv: list[str]) -> tuple[list[str], str, str, str]:
    """IN OUT_DIR NAME HEIGHT [--lod MORE.glb ...] -> ([IN, MORE...], OUT_DIR, NAME, HEIGHT)."""
    if "--lod" in argv:
        at = argv.index("--lod")
        head, lods = argv[:at], argv[at + 1:]
    else:
        head, lods = argv, []
    if len(head) != 4:
        raise SystemExit("usage: IN.glb OUT_DIR NAME HEIGHT [--lod MORE.glb ...]")
    source, out_dir, name, height = head
    return [source, *lods], out_dir, name, height


def lod_object_name(name: str, level: int, index: int = 0) -> str:
    """Unity makes a LODGroup from meshes whose names END in _LOD<n>, so a second mesh
    in one level gets its counter before the suffix, never after."""
    return f"{name}_LOD{level}" if index == 0 else f"{name}_{index}_LOD{level}"


def linked_image(socket):
    """The image feeding a socket, through a normal-map or separate-colour node if any."""
    if socket is None or not socket.is_linked:
        return None
    node = socket.links[0].from_node
    for _ in range(4):
        if node.type == "TEX_IMAGE":
            return node.image
        inputs = [i for i in node.inputs if i.is_linked]
        if not inputs:
            return None
        # A normal map's colour input, or a separate-colour's only input.
        preferred = next((i for i in inputs if i.name in ("Color", "Image")), inputs[0])
        node = preferred.links[0].from_node
    return None


def main() -> None:
    import bpy  # only inside Blender

    from image_to_3dlab.unity_export import safe_name, scale_for_height, texture_file

    sources, out_dir, name, height = parse_args(sys.argv[sys.argv.index("--") + 1:])
    out = Path(out_dir)
    textures = out / "textures"
    textures.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    for level, source in enumerate(sources):
        before_objects, before_mats = set(bpy.data.objects), set(bpy.data.materials)
        bpy.ops.import_scene.gltf(filepath=source)
        if len(sources) > 1:
            new_meshes = [o for o in bpy.data.objects if o not in before_objects and o.type == "MESH"]
            for index, obj in enumerate(new_meshes):
                obj.name = lod_object_name(safe_name(name), level, index)
            for mat in set(bpy.data.materials) - before_mats:
                mat.name = f"{safe_name(name)}_LOD{level}_{mat.name}"
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0

    shapes = {pb.custom_shape for o in scene.objects if o.type == "ARMATURE"
              for pb in o.pose.bones if pb.custom_shape is not None}
    for o in scene.objects:
        if o.type == "ARMATURE":
            for pb in o.pose.bones:
                pb.custom_shape = None
    for shape in shapes:
        bpy.data.objects.remove(shape, do_unlink=True)

    meshes = [o for o in scene.objects if o.type == "MESH"]
    scale = scale_for_height(mesh_height(meshes), "custom", float(height) or None)
    roots = [o for o in scene.objects if o.parent is None]
    for obj in roots:
        obj.scale = [s * scale for s in obj.scale]
    bpy.context.view_layer.update()

    # Pivot at the feet, centred, and turned to face Unity's +Z. Then bake it all in so
    # the FBX carries no root transform for Unity to inherit.
    from mathutils import Matrix, Vector

    lo, hi = mesh_bounds(meshes)
    offset = Vector(ground_offset(lo, hi))
    turn = Matrix.Rotation(math.radians(UNITY_FACING_TURN), 4, "Z")
    for obj in roots:
        obj.matrix_world = turn @ Matrix.Translation(offset) @ obj.matrix_world
    bpy.ops.object.select_all(action="SELECT")
    bpy.context.view_layer.objects.active = roots[0] if roots else None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    materials = []
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.users == 0:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        entry = {"name": mat.name,
                 "baseColorFactor": list(bsdf.inputs["Base Color"].default_value),
                 "metallic": float(bsdf.inputs["Metallic"].default_value),
                 "smoothness": 1.0 - float(bsdf.inputs["Roughness"].default_value)}
        sockets = {
            "baseColor": bsdf.inputs.get("Base Color"),
            "normal": bsdf.inputs.get("Normal"),
            "metalRough": bsdf.inputs.get("Roughness") or bsdf.inputs.get("Metallic"),
            "emission": bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission"),
        }
        # glTF occlusion sits on the importer's "glTF Material Output" group, not the BSDF.
        group = next((n for n in mat.node_tree.nodes
                      if n.type == "GROUP" and "glTF" in (n.node_tree.name if n.node_tree else "")), None)
        if group is not None:
            sockets["occlusion"] = group.inputs.get("Occlusion")
        for role, socket in sockets.items():
            image = linked_image(socket)
            if image is None:
                continue
            filename = (f"{safe_name(mat.name)}_metalRough.png" if role == "metalRough"
                        else texture_file(mat.name, role))
            image.filepath_raw = str(textures / filename)
            image.file_format = "PNG"
            if image.packed_file is not None or image.source == "FILE":
                image.save()
            entry[role] = filename
        if "baseColor" in entry:
            # A linked texture overrides the socket's colour, so its value means nothing.
            entry["baseColorFactor"] = [1.0, 1.0, 1.0, entry["baseColorFactor"][3]]
        materials.append(entry)

    armatures = [o for o in scene.objects if o.type == "ARMATURE"]
    has_animations = bool(bpy.data.actions)
    unweighted = 0
    if armatures:
        for obj in meshes:
            unweighted += sum(1 for v in obj.data.vertices if not any(g.weight > 0 for g in v.groups))

    fbx = out / f"{safe_name(name)}.fbx"
    bpy.ops.export_scene.fbx(**fbx_kwargs(str(fbx), has_animations))
    print("I2L_UNITY_EXPORT " + json.dumps({
        "fbx": str(fbx),
        "materials": materials,
        "armature": armatures[0].name if armatures else None,
        "bones": len(armatures[0].data.bones) if armatures else 0,
        "unweighted_vertices": unweighted,
        "has_animations": has_animations,
        "lods": len(sources),
        "height": mesh_height(meshes),
        "scale": scale,
    }), flush=True)


if __name__ == "__main__":
    main()
