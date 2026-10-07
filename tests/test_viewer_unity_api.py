"""viewer/unity_api.py: settings, commands, stage parsing and job creation."""

from __future__ import annotations

import io

import pytest
import unity_api as api
from PIL import Image


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 10, 10)).save(buf, "JPEG")
    return buf.getvalue()


def test_normalise_settings_clamps_and_validates():
    s = api.normalise_settings({"class": "quadruped", "faces": 10, "seed": -1, "height": "0.7",
                                "name": "Big Wolf!"})
    assert s["class"] == "quadruped" and s["faces"] == 2000 and s["height"] == 0.7
    assert 0 <= s["seed"] < 2 ** 31 and s["name"] == "Big_Wolf"
    assert api.normalise_settings({})["class"] == "humanoid"
    with pytest.raises(ValueError):
        api.normalise_settings({"class": "dragon"})
    with pytest.raises(ValueError):
        api.normalise_settings({"height": 900})


def test_parse_stage_and_progress():
    assert api.parse_stage("I2L_STAGE::rig::start") == ("rig", "start")
    assert api.parse_stage("I2L_STAGE::paint::start") is None
    assert api.parse_stage("hello") is None
    assert api.overall_pct("generate", False) == 0
    assert api.overall_pct("generate", True) == 60
    assert api.overall_pct("export", True) == 100


def test_create_writes_png_and_builds_command(tmp_path):
    manager = api.UnityJobManager(tmp_path)
    job = manager.create("my hero.jpg", png_bytes(), {"class": "humanoid", "height": 1.7})
    assert job.name == "my_hero"
    with Image.open(job.image_path) as im:
        assert im.format == "PNG"
    cmd = api.build_command(job)
    assert cmd[1].endswith("image_to_unity.py")
    assert cmd[cmd.index("--class") + 1] == "humanoid"
    assert cmd[cmd.index("--height") + 1] == "1.7"
    assert "--resume" not in cmd
    assert manager.busy()
    with pytest.raises(RuntimeError):
        manager.create("again.png", png_bytes(), {})
    job.status = "done"
    manager.finish(job)
    assert not manager.busy()


def test_create_rejects_non_images(tmp_path):
    with pytest.raises(ValueError):
        api.UnityJobManager(tmp_path).create("model.glb", b"x", {})


def test_preview_prefers_rigged(tmp_path):
    job = api.UnityJob("a" * 32, tmp_path, "hero")
    (tmp_path / "steps").mkdir()
    assert job.preview_glb.name == "2_finished.glb"
    (tmp_path / "steps" / "3_rigged.glb").write_text("x")
    assert job.preview_glb.name == "3_rigged.glb"
    assert job.zip_path == tmp_path / "unity" / "hero.zip"


def test_get_rejects_bad_ids(tmp_path):
    assert api.UnityJobManager(tmp_path).get("../etc") is None
