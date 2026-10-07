#!/usr/bin/env python3
"""Export every finished prop of a prop sheet for Unity: one FBX per prop, its LODs inside.

    python scripts/unity_export_props.py output/sheet/finished output/sheet/unity
    python scripts/unity_export_props.py output/sheet/finished output/sheet/unity --unity-project ~/MyGame

Reads `scripts/finish_props.py`'s output (`<prop>/<prop>_LOD<n>.glb`) and runs
`scripts/unity_export.py` on each prop with all its LODs, so Unity builds a LODGroup per
prop on import. Props keep the size they were generated at; Unity's pivot sits at each
prop's base. Writes `OUT_DIR/<prop>/` per prop and `OUT_DIR/props.unity.json`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import unity_export as export_one  # scripts/unity_export.py

from image_to_3dlab import unity_export

LOD_FILE = re.compile(r"^(?P<prop>.+)_LOD(?P<level>\d+)\.glb$")


def find_props(finished: Path) -> dict[str, list[Path]]:
    """{prop: [LOD0, LOD1, ...]} from a finish_props output folder. Compressed
    `.web.glb` copies are skipped: FBX export wants the plain files."""
    props: dict[str, list[tuple[int, Path]]] = {}
    for path in sorted(finished.glob("*/*_LOD*.glb")):
        if path.name.endswith(".web.glb"):
            continue
        match = LOD_FILE.match(path.name)
        if match and match["prop"] == path.parent.name:
            props.setdefault(match["prop"], []).append((int(match["level"]), path))
    return {prop: [p for _, p in sorted(levels)] for prop, levels in sorted(props.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("finished", type=Path, help="finish_props.py's OUT_DIR")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--unity-project", type=Path, help="copy every prop into this project")
    args = parser.parse_args(argv)

    props = find_props(args.finished)
    if not props:
        raise SystemExit(f"no <prop>/<prop>_LOD<n>.glb files under {args.finished}")
    summary = {}
    for prop, lods in props.items():
        out = args.out_dir / unity_export.safe_name(prop)
        result = export_one.export(lods[0], out, prop, rig_class="none", lods=lods[1:])
        if args.unity_project:
            result["installed"] = str(unity_export.install_into_project(
                out, args.unity_project.expanduser()))
        summary[prop] = result
        print(f"[props] {prop}: {len(lods)} LODs -> {result['fbx']}", flush=True)
    (args.out_dir / "props.unity.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
