"""The data-drive setting: where caches and heavy folders go."""

from __future__ import annotations

import os

import pytest

from image_to_3dlab import data_root as dr


def test_no_root_means_defaults(tmp_path):
    env: dict[str, str] = {}
    assert dr.apply_env(env, repo=tmp_path) is None
    assert env == {}


def test_env_var_wins_over_pointer_file(tmp_path):
    (tmp_path / dr.POINTER_FILE).write_text("/from/file\n")
    assert dr.data_root({dr.ENV_VAR: "/from/env"}, repo=tmp_path) == dr.Path("/from/env")
    assert dr.data_root({}, repo=tmp_path) == dr.Path("/from/file")


def test_apply_env_defaults_caches_but_keeps_existing(tmp_path):
    env = {dr.ENV_VAR: str(tmp_path / "data"), "HF_HOME": "/mine"}
    root = dr.apply_env(env, repo=tmp_path)
    assert root == tmp_path / "data"
    assert env["HF_HOME"] == "/mine"
    assert env["U2NET_HOME"] == str(tmp_path / "data" / "u2net")
    assert set(dr.CACHE_DIRS) <= set(env)


def test_link_dirs_moves_contents_and_is_idempotent(tmp_path):
    repo, root = tmp_path / "repo", tmp_path / "data"
    (repo / "vendor" / "thing").mkdir(parents=True)
    (repo / "vendor" / "thing" / "w.bin").write_text("weights")
    dr.link_dirs(root, repo=repo)
    vendor = repo / "vendor"
    assert vendor.is_symlink()
    assert (root / "repo" / "vendor" / "thing" / "w.bin").read_text() == "weights"
    assert (vendor / "thing" / "w.bin").read_text() == "weights"
    # Folders that did not exist get a link anyway, so the first download lands there.
    assert (repo / "hunyuan_mlx" / "shape" / "weights").is_symlink()
    assert dr.link_plan(root, repo=repo) == []
    assert dr.link_dirs(root, repo=repo) == []


def test_link_dirs_refuses_to_drop_a_populated_foreign_link(tmp_path):
    repo, root, other = tmp_path / "repo", tmp_path / "data", tmp_path / "other"
    (other / "x").mkdir(parents=True)
    repo.mkdir()
    (repo / ".venv").symlink_to(other)
    with pytest.raises(RuntimeError):
        dr.link_dirs(root, repo=repo)


def test_inside_does_not_follow_symlinks(tmp_path):
    (tmp_path / "far").mkdir()
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "out").symlink_to(tmp_path / "far")
    path = tmp_path / "repo" / "out" / "a.glb"
    assert dr.inside(path, (tmp_path / "repo",))
    assert not dr.inside(os.path.realpath(tmp_path / "repo" / "out"), (tmp_path / "repo",))
