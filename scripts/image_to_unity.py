#!/usr/bin/env python3
"""One image in, a rigged Unity-ready character out: generate, finish, rig, export.

    python scripts/image_to_unity.py hero.png output/unity/hero --class humanoid
    python scripts/image_to_unity.py wolf.png output/unity/wolf --class quadruped --faces 30000
    python scripts/image_to_unity.py crate.png output/unity/crate --class none
    python scripts/image_to_unity.py hero.png output/unity/hero --unity-project ~/MyGame

Stages, each a script that also runs alone:
  1. generate  `pixal3d_generate.py`  image -> textured high-poly GLB (MIT + DINOv3)
  2. finish    `retopo_repaint.py`    retopologise to --faces, bake detail, Pixel Match
  3. rig       `autorig.py`           SkinTokens, else a template skeleton (skipped for none)
  4. export    `unity_export.py`      FBX + URP textures + manifest (+ copy into a project)

Everything lands in OUT_DIR: `steps/` holds each stage's output and log, `unity/NAME/` is
the folder to drag into Unity, and `run.json` records settings, timings and the licence of
every component. `--resume` reuses finished stages. Prints `I2L_STAGE::<stage>::<msg>`
lines for the viewer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import autorig, provenance, unity_export

SCRIPTS = REPO / "scripts"
STAGES = ("generate", "finish", "rig", "export")
CLASSES = (*autorig.RIG_CLASSES, "none")


@dataclass(frozen=True)
class Plan:
    image: Path
    out: Path
    name: str
    rig_class: str = "humanoid"
    faces: int = 30000
    seed: int = 42
    height: float | None = None
    unity_project: Path | None = None

    @property
    def steps(self) -> Path:
        return self.out / "steps"

    @property
    def raw(self) -> Path:
        return self.steps / "1_generated.glb"

    @property
    def finished(self) -> Path:
        return self.steps / "2_finished.glb"

    @property
    def rigged(self) -> Path:
        return self.steps / "3_rigged.glb"

    @property
    def export_dir(self) -> Path:
        return self.out / "unity" / self.name

    @property
    def export_source(self) -> Path:
        return self.finished if self.rig_class == "none" else self.rigged

    def artifact(self, stage: str) -> Path:
        return {"generate": self.raw, "finish": self.finished, "rig": self.rigged,
                "export": self.export_dir / f"{self.name}{unity_export.MANIFEST_SUFFIX}"}[stage]


def stage_command(plan: Plan, stage: str) -> list[str] | None:
    """The command for one stage, or None when the stage does not apply."""
    py = sys.executable
    if stage == "generate":
        return [py, str(SCRIPTS / "pixal3d_generate.py"), str(plan.image), str(plan.raw),
                "--seed", str(plan.seed)]
    if stage == "finish":
        cmd = [py, str(SCRIPTS / "retopo_repaint.py"), str(plan.raw), str(plan.image),
               str(plan.finished), "--faces", str(plan.faces), "--skip-paint",
               "--steps-dir", str(plan.steps / "finish")]
        views = plan.raw.with_suffix(".svviews")
        if (views / "transforms.json").is_file():
            cmd += ["--views", str(views)]  # Pixel Match: the photo's own pixels back on
        return cmd
    if stage == "rig":
        if plan.rig_class == "none":
            return None
        return [py, str(SCRIPTS / "autorig.py"), str(plan.finished), str(plan.rigged),
                "--class", plan.rig_class, "--seed", str(plan.seed)]
    if stage == "export":
        cmd = [py, str(SCRIPTS / "unity_export.py"), str(plan.export_source), str(plan.export_dir),
               "--name", plan.name, "--class", plan.rig_class, "--zip"]
        if plan.height is not None:
            cmd += ["--height", str(plan.height)]
        if plan.unity_project is not None:
            cmd += ["--unity-project", str(plan.unity_project)]
        return cmd
    raise ValueError(f"unknown stage {stage!r}")


def done(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def emit(stage: str, message: str) -> None:
    print(f"I2L_STAGE::{stage}::{message}", flush=True)


def components(plan: Plan) -> list[dict]:
    """Every model and tool the run used, with its licence, for run.json."""
    pixal = provenance.LICENSES["pixal3d"]
    out = [{"stage": "generate", "component": "Pixal3D (raven38/pixal3d.cpp)",
            "license": pixal.license_name, "url": pixal.license_url},
           {"stage": "generate", "component": "BiRefNet-lite background remover",
            "license": provenance.MATTE_LICENSES["birefnet-general-lite"]},
           {"stage": "finish", "component": "Blender (retopology, bake, Pixel Match)",
            "license": "GPL-2.0+ (run as a separate program)"}]
    record = plan.rigged.with_suffix(".autorig.json")
    if plan.rig_class != "none" and record.is_file():
        for item in json.loads(record.read_text()).get("components", []):
            out.append({"stage": "rig", **item})
    out.append({"stage": "export", "component": "Blender FBX exporter + OpenMeshyImporter.cs",
                "license": "GPL-2.0+ (Blender, separate program); Apache-2.0 (this repo)"})
    return out


def run(plan: Plan, resume: bool = False, use_case: str = "game",
        distribution: str = "private") -> dict:
    # Licence gate before any model work, as for every run in this repo.
    provenance.validate_run_policy("pixal3d", use_case, distribution, allow_conditional=True)
    plan.steps.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    for stage in STAGES:
        command = stage_command(plan, stage)
        if command is None:
            emit(stage, "skipped (no rig for class none)")
            continue
        if resume and done(plan.artifact(stage)):
            emit(stage, "reused")
            continue
        emit(stage, "start")
        started = time.monotonic()
        log = plan.steps / f"{stage}.log"
        with log.open("w") as handle:
            process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
        timings[stage] = round(time.monotonic() - started, 1)
        if process.returncode != 0 or not done(plan.artifact(stage)):
            tail = "\n".join(log.read_text().splitlines()[-15:])
            emit(stage, f"failed (exit {process.returncode})")
            raise SystemExit(f"[{stage}] failed; see {log}\n{tail}")
        emit(stage, f"done in {timings[stage]:.0f}s")

    record = {
        "image": str(plan.image), "name": plan.name, "class": plan.rig_class,
        "faces": plan.faces, "seed": plan.seed, "height": plan.height,
        "use_case": use_case, "distribution": distribution,
        "timings_s": timings, "unity_folder": str(plan.export_dir),
        "zip": str(plan.export_dir) + ".zip",
        "components": components(plan),
    }
    rig_record = plan.rigged.with_suffix(".autorig.json")
    if rig_record.is_file():
        rig = json.loads(rig_record.read_text())
        record["rig"] = {k: rig.get(k) for k in ("ok", "route", "unity_rig", "bones")}
    (plan.out / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--name", help="asset name (default: the image's name)")
    parser.add_argument("--class", dest="rig_class", default="humanoid", choices=CLASSES)
    parser.add_argument("--faces", type=int, default=30000, help="target face count")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--height", type=float, help="metres (humanoids default to 1.8)")
    parser.add_argument("--unity-project", type=Path, help="copy the result into this project")
    parser.add_argument("--use-case", default="game", choices=("game", "showcase"))
    parser.add_argument("--distribution", default="private", choices=("private", "public", "worldwide"))
    parser.add_argument("--resume", action="store_true", help="reuse finished stages")
    args = parser.parse_args(argv)
    plan = Plan(args.image.resolve(), args.out.resolve(),
                unity_export.safe_name(args.name or args.image.stem), args.rig_class,
                args.faces, args.seed, args.height,
                args.unity_project.expanduser().resolve() if args.unity_project else None)
    record = run(plan, args.resume, args.use_case, args.distribution)
    print(f"I2L_IMAGE_TO_UNITY {json.dumps({'unity_folder': record['unity_folder'], 'rig': record.get('rig')})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
