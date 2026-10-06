"""scripts/relocate_data.py: pointer file, cache folders and links."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("relocate_data", REPO / "scripts" / "relocate_data.py")
relocate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocate)


def test_supports_symlinks_on_tmp(tmp_path):
    assert relocate.supports_symlinks(tmp_path / "d")


def test_main_writes_pointer_and_links(tmp_path, monkeypatch):
    repo, root = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    monkeypatch.setattr(relocate, "REPO", repo)
    assert relocate.main([str(root), "--yes"]) == 0
    assert (repo / ".i2l-data").read_text().strip() == str(root.resolve())
    assert (repo / "vendor").is_symlink()
    assert (root.resolve() / "hf").is_dir()


def test_main_stops_without_yes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(relocate, "REPO", repo)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert relocate.main([str(tmp_path / "data")]) == 1
    assert not (repo / ".i2l-data").exists()
