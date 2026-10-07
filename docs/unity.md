# Image → Unity

One picture in, a rigged, textured model out, ready for a Unity project. Everything runs on
this Mac; nothing is uploaded.

## Use it

**In the lab:** open the **Image → Unity** tab, pick an image, say what it is (humanoid,
four-legged creature, other creature, prop) and press **Make Unity model**. About 10–15
minutes on an M2 Pro. Download the zip when it's done.

**From a terminal:**

```bash
python scripts/image_to_unity.py hero.png output/unity/hero --class humanoid
python scripts/image_to_unity.py wolf.png output/unity/wolf --class quadruped
python scripts/image_to_unity.py hero.png output/unity/hero --unity-project ~/MyGame
```

`--unity-project` copies the result straight into `Assets/OpenMeshy/<name>/` and installs
the importer for you.

## What happens

| Stage | Tool | Licence |
|---|---|---|
| Generate | Pixal3D (`pixal3d_generate.py`) | MIT code and weights; DINOv3 License (encoder) |
| Finish | Blender retopology, detail bake, Pixel Match (`retopo_repaint.py`) | Blender is GPL, run as a separate program |
| Rig | SkinTokens, else a template skeleton (`autorig.py`) | MIT weights; binary GPL-3.0, run as a separate program |
| Export | Blender FBX + `OpenMeshyImporter.cs` (`unity_export.py`) | Apache-2.0 (this repo) |

`run.json` in each run folder records the settings, timings and every component's
licence. The rigs and meshes are model output and yours to ship. Hunyuan3D is not used here
(its licence excludes the EU, UK and South Korea).

## Importing into Unity

1. **Once per project:** copy `unity/OpenMeshy/Editor/OpenMeshyImporter.cs` into
   `Assets/OpenMeshy/Editor/`. One copy only; two copies of a class will not compile.
2. Unzip the download anywhere under `Assets/`.
3. The importer reads `<name>.openmeshy.json` and sets:
   - **Rig:** Humanoid when the skeleton has every bone Unity's avatar needs, else Generic.
   - **Scale:** 1:1, no stray transform. Humanoids arrive 1.8 m tall unless you chose a height.
   - **Axes:** Bake Axis Conversion on, so the model stands up with no -90° rotation.
   - **Materials:** URP Lit (Standard on the built-in pipeline) with base colour, normal,
     metallic-smoothness, occlusion and emission wired up.

FBX files without a manifest beside them are left alone.

## Animating

Humanoids use Mixamo bone names (`mixamorig:Hips`, ...). Any Humanoid animation retargets:
Mixamo clips, Unity's starter assets, the Asset Store. Set the clip's rig to Humanoid too.

Creatures get a Generic rig. Animate them with clips made for that skeleton, or in Blender.

## When the rig is wrong

- **Learned rig failed** (`run.json` → `rig.route` is `template`): SkinTokens needs a clear
  body shape. A front-facing, arms-out, whole-body image helps most.
- **Arms do not move:** the arms are separate pieces in the mesh. The template's voxel
  fallback keeps only the biggest piece. Regenerate with arms away from the body.
- Re-run one stage by hand: `python scripts/autorig.py steps/2_finished.glb rigged.glb
  --class humanoid --seed 3`, then `scripts/unity_export.py`.
