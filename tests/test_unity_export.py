"""image_to_3dlab.unity_export and scripts/unity_export.py, without Blender."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

from image_to_3dlab import unity_export as ue

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unity_export_script", REPO / "scripts" / "unity_export.py")
script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = script
spec.loader.exec_module(script)
spec2 = importlib.util.spec_from_file_location("bue", REPO / "scripts" / "blender_unity_export.py")
bue = importlib.util.module_from_spec(spec2)
sys.modules[spec2.name] = bue
spec2.loader.exec_module(bue)


def test_safe_names():
    assert ue.safe_name("Material.001 (skin)") == "Material_001_skin"
    assert ue.safe_name("???") == "asset"
    assert ue.texture_file("Body", "normal") == "Body_normal.png"
    with pytest.raises(ValueError):
        ue.texture_file("Body", "shiny")


def test_scale_for_height():
    assert ue.scale_for_height(1.0, "humanoid") == pytest.approx(1.8)
    assert ue.scale_for_height(2.0, "humanoid", 1.0) == pytest.approx(0.5)
    assert ue.scale_for_height(1.0, "quadruped") == 1.0
    assert ue.scale_for_height(0.0, "humanoid") == 1.0


def test_pack_metallic_smoothness():
    src = Image.new("RGB", (2, 1), (0, 64, 200))  # roughness 64, metallic 200
    out = ue.pack_metallic_smoothness(src)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0)) == (200, 0, 0, 255 - 64)


def test_manifest_shape():
    m = ue.build_manifest("Bot", "Humanoid", [{"name": "Body", "baseColor": "Body_baseColor.png"}],
                          False, 1.8, "x.glb", {"route": "skintokens", "components": [{"c": 1}]})
    assert m["rig"] == "Humanoid" and m["version"] == 1
    mat = m["materials"][0]
    assert mat["baseColor"] == "Body_baseColor.png" and mat["normal"] == ""
    assert set(ue.TEXTURE_ROLES) <= set(mat)
    assert m["autorig"]["route"] == "skintokens"
    with pytest.raises(ValueError):
        ue.build_manifest("Bot", "Human", [], False, 1, "x", None)


def test_importer_reads_the_manifest_fields():
    """The C# classes must name every field the manifest writes, or JsonUtility drops it."""
    source = ue.IMPORTER.read_text()
    m = ue.build_manifest("Bot", "Generic", [{"name": "Body"}], True, 1, "x", None)
    for key in ("version", "name", "rig", "hasAnimations", "appliedScale", "materials"):
        assert f" {key};" in source, key
    for key in m["materials"][0]:
        assert f" {key};" in source, key
    assert ue.MANIFEST_SUFFIX in source


def test_install_into_project(tmp_path):
    export = tmp_path / "exports" / "Bot"
    (export / "textures").mkdir(parents=True)
    (export / "Bot.fbx").write_text("fbx")
    project = tmp_path / "Game"
    with pytest.raises(ValueError):
        ue.install_into_project(export, project)
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    dest = ue.install_into_project(export, project)
    assert (dest / "Bot.fbx").read_text() == "fbx"
    assert (project / "Assets/OpenMeshy/Editor/OpenMeshyImporter.cs").is_file()
    assert ue.install_into_project(export, project) == dest  # re-export replaces


def test_finish_textures_repacks(tmp_path):
    (tmp_path / "textures").mkdir()
    Image.new("RGB", (1, 1), (0, 0, 255)).save(tmp_path / "textures" / "Body_metalRough.png")
    mats = script.finish_textures(tmp_path, [{"name": "Body", "metalRough": "Body_metalRough.png",
                                              "metallic": 0.2, "smoothness": 0.3}])
    assert mats[0]["metallicSmoothness"] == "Body_metallicSmoothness.png"
    assert "metalRough" not in mats[0]
    assert mats[0]["metallic"] == 1.0
    assert not (tmp_path / "textures" / "Body_metalRough.png").exists()
    with Image.open(tmp_path / "textures" / "Body_metallicSmoothness.png") as im:
        assert im.getpixel((0, 0)) == (255, 0, 0, 255)


def test_export_command_and_marker():
    cmd = script.export_command(Path("/b"), Path("in.glb"), Path("out"), "Bot", 1.8)
    assert cmd[5].endswith("blender_unity_export.py")
    assert cmd[6:] == ["--", "in.glb", "out", "Bot", "1.8000"]
    assert script.target_height("humanoid", None) == 1.8
    assert script.target_height("quadruped", None) == 0.0
    assert script.target_height("quadruped", 0.6) == 0.6
    assert script.parse_marker('a\nI2L_UNITY_EXPORT {"fbx": "x"}') == {"fbx": "x"}
    with pytest.raises(RuntimeError):
        script.parse_marker("nothing")


def test_fbx_settings_suit_unity():
    kw = bue.fbx_kwargs("x.fbx", False)
    assert (kw["axis_forward"], kw["axis_up"]) == ("-Z", "Y")
    assert kw["add_leaf_bones"] is False and kw["use_armature_deform_only"] is True
    assert kw["apply_scale_options"] == "FBX_SCALE_UNITS"
    assert kw["bake_anim"] is False and bue.fbx_kwargs("x", True)["bake_anim"] is True
