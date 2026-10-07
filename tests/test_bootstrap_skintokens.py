"""scripts/bootstrap_skintokens.py: what it says before fetching, and that it asks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_skintokens", REPO / "scripts" / "bootstrap_skintokens.py")
boot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = boot
spec.loader.exec_module(boot)


def test_plan_names_tool_route_size_and_licence(tmp_path):
    text = "\n".join(boot.plan_lines(tmp_path, have_cargo=False, built=False, weights=False))
    assert "SkinTokens" in text and "1.1 GB" in text and "MIT" in text and "GPL-3.0" in text
    assert "rustup" in text
    text = "\n".join(boot.plan_lines(tmp_path, have_cargo=True, built=True, weights=True))
    assert "rustup" not in text and "already built" in text and "present" in text


def test_clone_is_pinned(tmp_path):
    cmds = boot.clone_commands(tmp_path)
    assert cmds[1][-1] == boot.autorig.SKINTOKENS_COMMIT


def test_find_cargo_prefers_cargo_home(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "cargo").write_text("")
    assert boot.find_cargo({"CARGO_HOME": str(tmp_path), "PATH": ""}) == tmp_path / "bin" / "cargo"


def test_refuses_without_yes_and_no_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(boot.autorig, "SKINTOKENS_DIR", tmp_path)
    monkeypatch.setattr(boot.sys.stdin, "isatty", lambda: False, raising=False)
    assert boot.main([]) == 2
