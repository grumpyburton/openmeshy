"""viewer/tools_api.py: path checks and the command each tool runs."""

from __future__ import annotations

import pytest
import tools_api as api


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "allowed_roots", lambda: (tmp_path,))
    path = tmp_path / "hero.glb"
    path.write_bytes(b"glTF")
    return path


def test_local_path_must_be_inside_and_exist(model, tmp_path):
    assert api.local_path(str(model)) == model
    with pytest.raises(ValueError):
        api.local_path("/etc/hosts")
    with pytest.raises(ValueError):
        api.local_path(str(tmp_path / "missing.glb"))
    with pytest.raises(ValueError):
        api.local_path(str(tmp_path / ".." / "escape.glb"))
    with pytest.raises(ValueError):
        api.local_path("")


def test_autorig_plan(model, tmp_path):
    cmd, outputs = api.plan("autorig", {"model": str(model), "class": "quadruped", "seed": 4},
                            tmp_path / "run")
    assert cmd[1].endswith("autorig.py") and cmd[2] == str(model)
    assert cmd[cmd.index("--class") + 1] == "quadruped" and cmd[-1] == "4"
    assert outputs["rigged"].name == "hero_rigged.glb"
    with pytest.raises(ValueError):
        api.plan("autorig", {"model": str(model), "class": "dragon"}, tmp_path)


def test_unity_export_plan_with_lods_and_project(model, tmp_path):
    lod = tmp_path / "hero_lod1.glb"
    lod.write_bytes(b"glTF")
    project = tmp_path / "Game"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    cmd, outputs = api.plan("unity_export", {"model": str(model), "class": "none",
                                             "lods": [str(lod)], "unity_project": str(project),
                                             "height": 2}, tmp_path / "run")
    assert cmd[cmd.index("--lod") + 1] == str(lod)
    assert cmd[cmd.index("--unity-project") + 1] == str(project)
    assert cmd[cmd.index("--height") + 1] == "2.0"
    assert outputs["manifest"].name == "hero.openmeshy.json"
    with pytest.raises(ValueError):
        api.plan("unity_export", {"model": str(model), "unity_project": str(tmp_path)}, tmp_path)


def test_unknown_tool(model, tmp_path):
    with pytest.raises(ValueError):
        api.plan("paint", {}, tmp_path)


def test_manager_runs_one_at_a_time(model, tmp_path):
    manager = api.ToolJobManager(tmp_path / "out")
    job = manager.create("autorig", {"model": str(model)})
    assert job.directory.is_dir() and manager.busy()
    with pytest.raises(RuntimeError):
        manager.create("autorig", {"model": str(model)})
    job.status = "done"
    manager.finish(job)
    assert not manager.busy()
    assert manager.get("../x") is None
