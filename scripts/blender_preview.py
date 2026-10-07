#!/usr/bin/env python3
"""Render a quick front-and-side preview of a GLB to one PNG, headless.

    blender --background --python-exit-code 1 --python scripts/blender_preview.py -- \
        IN.glb OUT.png [--size 512]

Two orthographic views side by side (front, then the model's left side), textured and
studio-lit with Blender's Workbench engine: a second or two, no GPU bake. Made for
looking at a result without opening Blender, by a person or an agent (the MCP server's
`render_preview`). Bone display shapes the glTF importer adds are dropped, so a rigged
model shows only its mesh.
"""

from __future__ import annotations

import math
import sys


def views() -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """(camera direction from the model, camera rotation in degrees) for each view.
    Generated models face glTF's front, which is Blender's -Y."""
    return [((0.0, -1.0, 0.0), (90.0, 0.0, 0.0)),    # front
            ((1.0, 0.0, 0.0), (90.0, 0.0, 90.0))]    # the model's left side


def parse_args(argv: list[str]) -> tuple[str, str, int]:
    import argparse

    parser = argparse.ArgumentParser(prog="blender_preview.py")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--size", type=int, default=512, help="height of each view in pixels")
    args = parser.parse_args(argv)
    return args.source, args.output, max(64, min(args.size, 2048))


def main() -> None:
    import bpy  # only inside Blender
    import numpy as np
    from mathutils import Vector

    source, output, size = parse_args(sys.argv[sys.argv.index("--") + 1:])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source)
    scene = bpy.context.scene
    shapes = {pb.custom_shape for o in scene.objects if o.type == "ARMATURE"
              for pb in o.pose.bones if pb.custom_shape is not None}
    for obj in scene.objects:
        if obj.type == "ARMATURE":
            for pb in obj.pose.bones:
                pb.custom_shape = None
    for shape in shapes:
        bpy.data.objects.remove(shape, do_unlink=True)

    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"{source} has no mesh")
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    centre, extent = (lo + hi) / 2, max(hi - lo)

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x = round(size * 0.8)
    scene.render.resolution_y = size
    scene.world = bpy.data.worlds.new("preview")
    scene.world.color = (0.12, 0.12, 0.14)
    camera = bpy.data.objects.new("preview", bpy.data.cameras.new("preview"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.25
    scene.camera = camera

    tiles = []
    for index, (direction, rotation) in enumerate(views()):
        camera.location = centre + Vector(direction) * extent * 3
        camera.rotation_euler = tuple(math.radians(r) for r in rotation)
        path = f"{output}.view{index}.png"
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(path)
        width, height = image.size
        tiles.append(np.array(image.pixels[:]).reshape(height, width, 4))
        bpy.data.images.remove(image)
        import os
        os.remove(path)

    sheet = np.concatenate(tiles, axis=1)
    out = bpy.data.images.new("preview_sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    out.pixels = sheet.ravel()
    out.filepath_raw = output
    out.file_format = "PNG"
    out.save()
    print(f"I2L_PREVIEW {output}", flush=True)


if __name__ == "__main__":
    main()
