"""scripts/image_to_unity.py: stage commands, skipping, resume and the run record."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_to_unity", REPO / "scripts" / "image_to_unity.py")
itu = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = itu
spec.loader.exec_module(itu)


def plan(tmp_path, **kw):
    return itu.Plan(tmp_path / "hero.png", tmp_path / "out", "hero", **kw)


def test_generate_and_finish_commands(tmp_path):
    p = plan(tmp_path, faces=20000, seed=3)
    gen = itu.stage_command(p, "generate")
    assert gen[1].endswith("pixal3d_generate.py") and gen[-2:] == ["--seed", "3"]
    fin = itu.stage_command(p, "finish")
    assert fin[1].endswith("retopo_repaint.py")
    assert fin[2:5] == [str(p.raw), str(p.image), str(p.finished)]
    assert "--faces" in fin and "20000" in fin and "--views" not in fin


def test_finish_uses_pixel_match_cameras_when_present(tmp_path):
    p = plan(tmp_path)
    views = p.raw.with_suffix(".svviews")
    views.mkdir(parents=True)
    (views / "transforms.json").write_text("{}")
    fin = itu.stage_command(p, "finish")
    assert fin[fin.index("--views") + 1] == str(views)


def test_rig_and_export(tmp_path):
    p = plan(tmp_path, rig_class="quadruped", height=0.7, unity_project=tmp_path / "Game")
    rig = itu.stage_command(p, "rig")
    assert rig[1].endswith("autorig.py") and rig[rig.index("--class") + 1] == "quadruped"
    exp = itu.stage_command(p, "export")
    assert exp[2] == str(p.rigged)
    assert exp[exp.index("--height") + 1] == "0.7"
    assert exp[exp.index("--unity-project") + 1] == str(tmp_path / "Game")


def test_class_none_skips_rig_and_exports_finished(tmp_path):
    p = plan(tmp_path, rig_class="none")
    assert itu.stage_command(p, "rig") is None
    assert itu.stage_command(p, "export")[2] == str(p.finished)


def test_unknown_stage(tmp_path):
    with pytest.raises(ValueError):
        itu.stage_command(plan(tmp_path), "paint")


def fake_runner(p, calls):
    def run(command, stdout, stderr, check):
        calls.append(command[1].rsplit("/", 1)[-1])
        stage = {"pixal3d_generate.py": "generate", "retopo_repaint.py": "finish",
                 "autorig.py": "rig", "unity_export.py": "export"}[calls[-1]]
        target = p.artifact(stage)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        if stage == "rig":
            p.rigged.with_suffix(".autorig.json").write_text(json.dumps(
                {"ok": True, "route": "skintokens", "unity_rig": "Humanoid", "bones": 22,
                 "components": [{"component": "SkinTokens", "license": "MIT"}]}))

        class Done:
            returncode = 0
        return Done()
    return run


def test_run_writes_record_and_resume_skips(tmp_path, monkeypatch):
    p = plan(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(itu.subprocess, "run", fake_runner(p, calls))
    record = itu.run(p)
    assert calls == ["pixal3d_generate.py", "retopo_repaint.py", "autorig.py", "unity_export.py"]
    saved = json.loads((p.out / "run.json").read_text())
    assert saved["rig"]["unity_rig"] == "Humanoid"
    assert {c["stage"] for c in saved["components"]} == {"generate", "finish", "rig", "export"}
    assert record["unity_folder"] == str(p.export_dir)
    calls.clear()
    itu.run(p, resume=True)
    assert calls == []


def test_failed_stage_stops(tmp_path, monkeypatch):
    p = plan(tmp_path)

    class Failed:
        returncode = 1

    monkeypatch.setattr(itu.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(SystemExit) as exc:
        itu.run(p)
    assert "[generate] failed" in str(exc.value)


def test_policy_gate_runs_first(tmp_path, monkeypatch):
    monkeypatch.setattr(itu.subprocess, "run", lambda *a, **k: pytest.fail("ran a stage"))
    with pytest.raises(ValueError):
        itu.run(plan(tmp_path), use_case="sale")
