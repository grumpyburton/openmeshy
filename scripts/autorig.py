#!/usr/bin/env python3
"""Rig a finished GLB automatically: SkinTokens first, a Blender template if that fails.

    python scripts/autorig.py model.glb rigged.glb --class humanoid
    python scripts/autorig.py beast.glb rigged.glb --class quadruped --seed 3
    python scripts/autorig.py model.glb rigged.glb --template-only

Feed it a retopologised mesh (Finish's output, tens of thousands of faces): the learned
rigger samples the surface, and a million-face raw generation only makes it slower.
Writes `rigged.autorig.json` beside the result: which route rigged it, the Unity rig type
it qualifies for (Humanoid / Generic), every problem found, and each tool's licence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import autorig
from image_to_3dlab.blender import find_blender

SCRIPTS = REPO / "scripts"

COMPONENTS = {
    "skintokens": {
        "component": "SkinTokens / TokenRig (VAST-AI) via chris-straka/skintokens",
        "license": "MIT weights and source; built binary GPL-3.0 (run as a separate program)",
        "url": "https://huggingface.co/VAST-AI/SkinTokens",
    },
    "template": {
        "component": "Blender template skeleton + automatic weights (scripts/blender_autorig_template.py)",
        "license": "Apache-2.0 (this repo); Blender GPL-2.0+ run as a separate program",
        "url": "https://www.blender.org",
    },
}


def template_command(blender: Path, source: Path, output: Path, rig_class: str) -> list[str]:
    return [str(blender), "--background", "--python-exit-code", "1",
            "--python", str(SCRIPTS / "blender_autorig_template.py"), "--",
            str(source), str(output), "--class", rig_class]


def choose_route(rig_class: str, template_only: bool, skintokens_ready: bool) -> list[str]:
    """Routes to try, in order. The template only knows bipeds and quadrupeds."""
    routes = [] if template_only or not skintokens_ready else ["skintokens"]
    if rig_class in ("humanoid", "quadruped"):
        routes.append("template")
    return routes


def marker(text: str, prefix: str) -> dict | None:
    for line in text.splitlines():
        if line.startswith(prefix + " "):
            return json.loads(line[len(prefix) + 1:])
    return None


def run_skintokens(source: Path, output: Path, rig_class: str, seed: int) -> dict:
    report = output.with_suffix(".skintokens.json")
    command = autorig.rig_command(source, output, report, rig_class, seed)
    process = subprocess.run(command, env=autorig.skintokens_env(), capture_output=True,
                             text=True, check=False)
    result = autorig.read_report(report)
    result["exit"] = process.returncode
    if process.returncode != 0:
        result.setdefault("reason", (process.stderr or process.stdout)[-800:])
    return result


def run_template(source: Path, output: Path, rig_class: str) -> dict:
    blender = find_blender()
    if blender is None:
        return {"ok": False, "reason": "Blender not found (set I2L_BLENDER)"}
    process = subprocess.run(template_command(blender, source, output, rig_class),
                             capture_output=True, text=True, check=False)
    result = marker(process.stdout, "I2L_AUTORIG_TEMPLATE") or {"ok": False}
    result["exit"] = process.returncode
    if process.returncode != 0:
        result["ok"] = False
        result.setdefault("reason", (process.stderr or process.stdout)[-800:])
    return result


def rig(source: Path, output: Path, rig_class: str = "humanoid", seed: int = 0,
        template_only: bool = False) -> dict:
    """Try each route until one gives a skeleton that passes; return the record."""
    attempts = []
    record: dict = {"source": str(source), "output": str(output), "class": rig_class,
                    "seed": seed, "attempts": attempts}
    for route in choose_route(rig_class, template_only, autorig.skintokens_ready()):
        started = time.monotonic()
        result = (run_skintokens(source, output, rig_class, seed) if route == "skintokens"
                  else run_template(source, output, rig_class))
        skeleton = autorig.glb_skeleton(output) if output.is_file() and result.get("exit") == 0 else None
        problems = autorig.problems_for(rig_class, skeleton) if result.get("exit") == 0 else \
            [result.get("reason", "failed")]
        attempts.append({"route": route, "seconds": round(time.monotonic() - started, 1),
                         "problems": problems, "detail": result})
        print(f"[autorig] {route}: {'ok' if not problems else '; '.join(problems)}", flush=True)
        if not problems:
            record.update({
                "ok": True, "route": route, "bones": len(skeleton.joints),
                "unity_rig": autorig.unity_rig_type(rig_class, skeleton),
                "components": [COMPONENTS[route]],
            })
            return record
    record.update({"ok": False, "route": None, "unity_rig": "None", "components": []})
    if not attempts:
        record["reason"] = f"no route can rig class {rig_class!r} here (is SkinTokens installed?)"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--class", dest="rig_class", default="humanoid", choices=autorig.RIG_CLASSES)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--template-only", action="store_true", help="skip the learned rigger")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    record = rig(args.source, args.output, args.rig_class, args.seed, args.template_only)
    sidecar = args.output.with_suffix(".autorig.json")
    sidecar.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"I2L_AUTORIG {json.dumps({'ok': record['ok'], 'route': record['route'], 'unity_rig': record['unity_rig']})}")
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
