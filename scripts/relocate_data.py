#!/usr/bin/env python3
"""Move weights, caches, backend checkouts and outputs to another drive, with one setting.

    python scripts/relocate_data.py /Volumes/Data/i2l          # says what it will do, then asks
    python scripts/relocate_data.py /Volumes/Data/i2l --yes    # non-interactive
    python scripts/relocate_data.py --show                     # where things live now

Writes the path to `.i2l-data` (git-ignored), then turns the repo's heavy folders
(`vendor/`, `output/`, `.venv/`, Hunyuan weights...) into symlinks onto that drive,
moving anything already there. The lab and `pipeline.py` read `.i2l-data` at start and
point every model cache (Hugging Face, rembg, torch, uv, cargo) at the same drive.

The drive needs symlinks, so APFS. For an exFAT drive, make an APFS disk image on it:
`hdiutil create -size 120g -type SPARSEBUNDLE -fs APFS -volname I2LData X.sparsebundle`.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import data_root as dr


def supports_symlinks(folder: Path) -> bool:
    """Try one: exFAT and FAT drives refuse, and Python environments need them."""
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        try:
            (Path(tmp) / "link").symlink_to(Path(tmp))
        except OSError:
            return False
    return True


def show() -> int:
    root = dr.data_root()
    print(f"data root: {root or '(none: default locations)'}")
    for rel in dr.LINKED_DIRS:
        path = REPO / rel
        where = f"-> {path.readlink()}" if path.is_symlink() else ("in repo" if path.exists() else "absent")
        print(f"  {rel:28} {where}")
    if root:
        for name, value in dr.cache_env(root).items():
            print(f"  {name:28} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", type=Path, help="folder on the data drive")
    parser.add_argument("--yes", action="store_true", help="do not ask")
    parser.add_argument("--show", action="store_true", help="print where things live and stop")
    args = parser.parse_args(argv)
    if args.show or args.root is None:
        return show()

    root = args.root.expanduser().resolve()
    if not supports_symlinks(root):
        print(f"{root} cannot hold symlinks (exFAT or FAT?). Use an APFS disk image there.")
        return 2
    plan = dr.link_plan(root, REPO)
    print(f"Data root: {root}  (written to {dr.POINTER_FILE})")
    for link, target in plan:
        print(f"  {link.relative_to(REPO)} -> {target}")
    if not args.yes:
        answer = input("Go ahead? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Stopped. Nothing was changed.")
            return 1
    (REPO / dr.POINTER_FILE).write_text(f"{root}\n", encoding="utf-8")
    for folder in dr.CACHE_DIRS.values():
        (root / folder).mkdir(parents=True, exist_ok=True)
    dr.link_dirs(root, REPO)
    print("Done. Restart the lab so it picks up the new cache locations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
