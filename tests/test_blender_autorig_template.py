"""scripts/blender_autorig_template.py: the template skeletons, without Blender."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from image_to_3dlab import autorig

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tmpl", REPO / "scripts" / "blender_autorig_template.py")
tmpl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tmpl
spec.loader.exec_module(tmpl)

PERSON = tmpl.Box((-0.4, -0.15, 0.0), (0.4, 0.15, 1.8))   # T-pose: wide
ARMS_DOWN = tmpl.Box((-0.25, -0.15, 0.0), (0.25, 0.15, 1.8))
DOG = tmpl.Box((-0.2, -0.5, 0.0), (0.2, 0.5, 0.7))


def skeleton(joints):
    return autorig.Skeleton([j.name for j in joints], {j.name: j.parent for j in joints})


@pytest.mark.parametrize("box", [PERSON, ARMS_DOWN])
def test_humanoid_template_passes_unity_check(box):
    joints = tmpl.humanoid_joints(box)
    assert autorig.humanoid_problems(skeleton(joints)) == []
    names = {j.name for j in joints}
    assert all(j.parent is None or j.parent in names for j in joints)


def test_humanoid_is_symmetric_and_in_the_box():
    joints = {j.name: j for j in tmpl.humanoid_joints(PERSON)}
    left, right = joints["mixamorig:LeftHand"], joints["mixamorig:RightHand"]
    assert left.head[0] == pytest.approx(-right.head[0])
    assert left.head[0] > 0  # character's left is +X
    for j in joints.values():
        assert PERSON.min[2] - 1e-6 <= j.head[2] <= PERSON.max[2] + 1e-6
    assert joints["mixamorig:Head"].tail[2] == pytest.approx(1.8)


def test_t_pose_arms_are_level_and_hanging_arms_drop():
    t = {j.name: j for j in tmpl.humanoid_joints(PERSON)}["mixamorig:LeftHand"]
    down = {j.name: j for j in tmpl.humanoid_joints(ARMS_DOWN)}["mixamorig:LeftHand"]
    assert t.tail[2] > down.tail[2]


def test_quadruped_template():
    joints = tmpl.quadruped_joints(DOG)
    sk = skeleton(joints)
    assert autorig.generic_problems(sk) == []
    by = {j.name: j for j in joints}
    assert by["Head"].tail[1] < by["Tail"].tail[1]  # head at the front (-Y)
    assert {"FrontFoot.R", "BackFoot.R"} <= set(by)


def test_unknown_class():
    with pytest.raises(ValueError):
        tmpl.template_joints("custom", DOG)


def test_parse_args():
    assert tmpl.parse_args(["a.glb", "b.glb", "--class", "quadruped"]) == ("a.glb", "b.glb", "quadruped")


def test_voxel_fallback_threshold():
    assert not tmpl.needs_voxel_fallback(1, 100)
    assert tmpl.needs_voxel_fallback(3, 100)
    assert not tmpl.needs_voxel_fallback(0, 0)
    assert not tmpl.needs_voxel_fallback(4, 100, tmpl.MAX_UNWEIGHTED)
