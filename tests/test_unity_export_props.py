"""scripts/unity_export_props.py: finding props and their LODs in order."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unity_export_props", REPO / "scripts" / "unity_export_props.py")
props = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = props
spec.loader.exec_module(props)


def test_find_props_orders_lods_and_skips_web(tmp_path):
    for name in ("wagon_LOD1.glb", "wagon_LOD0.glb", "wagon_LOD10.glb", "wagon_LOD2.glb",
                 "wagon_LOD0.web.glb"):
        (tmp_path / "wagon").mkdir(exist_ok=True)
        (tmp_path / "wagon" / name).write_text("x")
    (tmp_path / "stump").mkdir()
    (tmp_path / "stump" / "stump_LOD0.glb").write_text("x")
    (tmp_path / "stray").mkdir()
    (tmp_path / "stray" / "other_LOD0.glb").write_text("x")
    found = props.find_props(tmp_path)
    assert list(found) == ["stump", "wagon"]
    assert [p.name for p in found["wagon"]] == ["wagon_LOD0.glb", "wagon_LOD1.glb",
                                                "wagon_LOD2.glb", "wagon_LOD10.glb"]


def test_main_needs_props(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        props.main([str(tmp_path), str(tmp_path / "out")])
