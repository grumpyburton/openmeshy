"""scripts/autorig.py: route order, fallback and the sidecar record."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from glb_fixtures import mixamo_doc, write_glb

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autorig_script", REPO / "scripts" / "autorig.py")
script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = script
spec.loader.exec_module(script)


def test_route_order():
    assert script.choose_route("humanoid", False, True) == ["skintokens", "template"]
    assert script.choose_route("humanoid", False, False) == ["template"]
    assert script.choose_route("humanoid", True, True) == ["template"]
    assert script.choose_route("custom", False, True) == ["skintokens"]
    assert script.choose_route("custom", False, False) == []


def test_template_command(tmp_path):
    cmd = script.template_command(Path("/b"), Path("in.glb"), Path("out.glb"), "quadruped")
    assert cmd[:4] == ["/b", "--background", "--python-exit-code", "1"]
    assert cmd[5].endswith("blender_autorig_template.py")
    assert cmd[6:] == ["--", "in.glb", "out.glb", "--class", "quadruped"]


def test_marker():
    assert script.marker("x\nI2L_AUTORIG_TEMPLATE {\"ok\": true}\n", "I2L_AUTORIG_TEMPLATE") == {"ok": True}
    assert script.marker("nothing", "I2L_AUTORIG_TEMPLATE") is None


def test_falls_back_when_learned_rig_fails_check(tmp_path, monkeypatch):
    out = tmp_path / "out.glb"
    monkeypatch.setattr(script.autorig, "skintokens_ready", lambda: True)

    def bad_skintokens(source, output, rig_class, seed):
        write_glb(output, mixamo_doc(drop=("LeftFoot",)))
        return {"ok": True, "exit": 0}

    def good_template(source, output, rig_class):
        write_glb(output, mixamo_doc())
        return {"ok": True, "exit": 0}

    monkeypatch.setattr(script, "run_skintokens", bad_skintokens)
    monkeypatch.setattr(script, "run_template", good_template)
    record = script.rig(tmp_path / "in.glb", out, "humanoid")
    assert record["ok"] and record["route"] == "template"
    assert record["unity_rig"] == "Humanoid"
    assert record["attempts"][0]["problems"] == ["missing bone LeftFoot"]
    assert "Blender" in record["components"][0]["component"]


def test_main_writes_sidecar_and_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(script.autorig, "skintokens_ready", lambda: False)
    rc = script.main([str(tmp_path / "in.glb"), str(tmp_path / "o" / "out.glb"), "--class", "custom"])
    assert rc == 1
    record = json.loads((tmp_path / "o" / "out.autorig.json").read_text())
    assert record["ok"] is False and "SkinTokens" in record["reason"]
