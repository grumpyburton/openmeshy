"""image_to_3dlab.autorig: commands, GLB skeleton reading and the Unity checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from glb_fixtures import mixamo_doc, skinned_doc, write_glb

from image_to_3dlab import autorig


def test_rig_command_shape(tmp_path):
    cmd = autorig.rig_command(Path("a.glb"), Path("b.glb"), Path("r.json"), "quadruped", 7,
                              root=tmp_path)
    assert cmd == [str(tmp_path / "target/release/skintokens"), "rig", "a.glb", "b.glb",
                   "--report", "r.json", "--class", "quadruped", "--seed", "7"]


def test_rig_command_rejects_unknown_class():
    with pytest.raises(ValueError):
        autorig.rig_command(Path("a"), Path("b"), Path("c"), "dragon")


def test_env_points_checkpoint_into_checkout(tmp_path):
    env = autorig.skintokens_env({}, root=tmp_path)
    assert env["SKINTOKENS_HOME"] == str(tmp_path / "home")
    assert autorig.skintokens_env({"SKINTOKENS_HOME": "/x"}, root=tmp_path)["SKINTOKENS_HOME"] == "/x"


def test_ready_needs_binary_and_weights(tmp_path):
    assert not autorig.skintokens_ready(tmp_path)
    (tmp_path / "target/release").mkdir(parents=True)
    (tmp_path / "target/release/skintokens").write_text("")
    assert not autorig.skintokens_ready(tmp_path)
    (tmp_path / "home/weights").mkdir(parents=True)
    (tmp_path / "home/weights/grpo_1400.ckpt").write_text("")
    assert autorig.skintokens_ready(tmp_path)


def test_glb_skeleton_reads_joints_and_parents(tmp_path):
    glb = write_glb(tmp_path / "m.glb", mixamo_doc())
    skeleton = autorig.glb_skeleton(glb)
    assert "mixamorig:Hips" in skeleton.joints
    assert skeleton.parents["mixamorig:Hips"] is None
    assert skeleton.parents["mixamorig:LeftHand"] == "mixamorig:LeftForeArm"
    assert "Hips" in skeleton.names


def test_unrigged_glb_has_no_skeleton(tmp_path):
    glb = write_glb(tmp_path / "m.glb", {"asset": {"version": "2.0"}, "nodes": [{"name": "x"}]})
    assert autorig.glb_skeleton(glb) is None
    assert autorig.humanoid_problems(None)
    assert autorig.unity_rig_type("humanoid", None) == "None"


def test_not_a_glb(tmp_path):
    (tmp_path / "x.glb").write_bytes(b"nope")
    with pytest.raises(ValueError):
        autorig.read_glb_json(tmp_path / "x.glb")


def test_full_mixamo_skeleton_is_humanoid(tmp_path):
    skeleton = autorig.glb_skeleton(write_glb(tmp_path / "m.glb", mixamo_doc()))
    assert autorig.humanoid_problems(skeleton) == []
    assert autorig.unity_rig_type("humanoid", skeleton) == "Humanoid"


def test_unprefixed_names_count_too(tmp_path):
    skeleton = autorig.glb_skeleton(write_glb(tmp_path / "m.glb", mixamo_doc(prefix="")))
    assert autorig.humanoid_problems(skeleton) == []


def test_missing_foot_falls_back_to_generic(tmp_path):
    skeleton = autorig.glb_skeleton(write_glb(tmp_path / "m.glb", mixamo_doc(drop=("LeftFoot",))))
    assert "missing bone LeftFoot" in autorig.humanoid_problems(skeleton)
    assert autorig.unity_rig_type("humanoid", skeleton) == "Generic"


def test_generic_checks_bone_count_and_root(tmp_path):
    doc = skinned_doc(["bone_0", "bone_1", "bone_2"], {"bone_0": None, "bone_1": "bone_0",
                                                        "bone_2": "bone_1"})
    skeleton = autorig.glb_skeleton(write_glb(tmp_path / "q.glb", doc))
    assert autorig.generic_problems(skeleton) == []
    assert autorig.unity_rig_type("quadruped", skeleton) == "Generic"
    two_roots = skinned_doc(["a", "b", "c"], {"a": None, "b": None, "c": "a"})
    skeleton = autorig.glb_skeleton(write_glb(tmp_path / "r.glb", two_roots))
    assert any("root" in p for p in autorig.generic_problems(skeleton))


def test_read_report_missing(tmp_path):
    assert autorig.read_report(tmp_path / "none.json")["ok"] is False
