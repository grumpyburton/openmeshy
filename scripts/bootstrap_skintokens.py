#!/usr/bin/env python3
"""Install SkinTokens, the automatic rigger: a Rust build of chris-straka/skintokens plus its 1.1 GB checkpoint.

    python scripts/bootstrap_skintokens.py          # says what it wants, then asks
    python scripts/bootstrap_skintokens.py --yes    # for the viewer and for agents

Clones the pinned commit into `vendor/skintokens/`, builds it with Metal, and fetches
VAST-AI's official checkpoint (MIT, checksummed) into `vendor/skintokens/home/weights/`.
Needs a Rust toolchain; when there is none it installs one with rustup (about 600 MB, into
`$CARGO_HOME`/`$RUSTUP_HOME`, which the data drive setting points at).

Licences: the checkpoint and most of the source are MIT; the shape encoder crate is
GPL-3.0, so the built binary is GPL-3.0 as a whole. This repo only runs it as a separate
program, and the rigs it writes are model output, free to ship.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from image_to_3dlab import autorig

RUSTUP_URL = "https://sh.rustup.rs"
RUST_MB = 600


def find_cargo(env: dict[str, str] | None = None) -> Path | None:
    env = dict(os.environ if env is None else env)
    home = env.get("CARGO_HOME")
    if home and (Path(home) / "bin" / "cargo").is_file():
        return Path(home) / "bin" / "cargo"
    found = shutil.which("cargo", path=env.get("PATH"))
    if found:
        return Path(found)
    default = Path.home() / ".cargo" / "bin" / "cargo"
    return default if default.is_file() else None


def plan_lines(root: Path, have_cargo: bool, built: bool, weights: bool) -> list[str]:
    lines = ["About to install:", "",
             "  tool:     SkinTokens automatic rigger (chris-straka/skintokens, Rust + Metal)",
             f"  route:    clone {autorig.SKINTOKENS_COMMIT} and build -> {root}"]
    if not have_cargo:
        lines.append(f"  rust:     rustup toolchain, about {RUST_MB} MB -> "
                     f"{os.environ.get('RUSTUP_HOME', '~/.rustup')}")
    if built:
        lines.append("  build:    already built, skipped")
    lines.append(f"  weights:  {autorig.SKINTOKENS_WEIGHTS_GB:.1f} GB -> {autorig.skintokens_home(root)}/weights/"
                 + (" (present, skipped)" if weights else ""))
    lines += ["", "  licence:  MIT checkpoint and source; built binary GPL-3.0 (run as a",
              "            separate program). https://huggingface.co/VAST-AI/SkinTokens"]
    return lines


def clone_commands(root: Path) -> list[list[str]]:
    return [["git", "clone", "--quiet", autorig.SKINTOKENS_UPSTREAM, str(root)],
            ["git", "-C", str(root), "checkout", "--quiet", autorig.SKINTOKENS_COMMIT]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="do not ask")
    args = parser.parse_args(argv)

    root = autorig.SKINTOKENS_DIR
    cargo = find_cargo()
    built = autorig.skintokens_binary(root).is_file()
    weights = (autorig.skintokens_home(root) / "weights" / "grpo_1400.ckpt").is_file()
    if built and weights:
        print(f"SkinTokens is already installed in {root}.")
        return 0
    print("\n".join(plan_lines(root, cargo is not None, built, weights)) + "\n")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to download without --yes when there is nobody to ask.")
            return 2
        if input("Go ahead? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Stopped. Nothing was changed.")
            return 1

    if cargo is None:
        print("Installing Rust with rustup...", flush=True)
        script = subprocess.run(["curl", "--proto", "=https", "--tlsv1.2", "-sSf", RUSTUP_URL],
                                capture_output=True, check=True).stdout
        subprocess.run(["sh", "-s", "--", "-y", "--no-modify-path", "--profile", "minimal"],
                       input=script, check=True)
        cargo = find_cargo()
        if cargo is None:
            raise SystemExit("rustup finished but cargo is still missing")

    if not (root / ".git").is_dir():
        for command in clone_commands(root):
            subprocess.run(command, check=True)
    if not built:
        print("Building (a few minutes the first time)...", flush=True)
        env = dict(os.environ, PATH=f"{cargo.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        subprocess.run([str(cargo), "build", "--release"], cwd=root, env=env, check=True)
    if not weights:
        subprocess.run(["bash", str(root / "fetch-weights.sh")], cwd=root,
                       env=autorig.skintokens_env(), check=True)
    subprocess.run([str(autorig.skintokens_binary(root)), "doctor"],
                   env=autorig.skintokens_env(), check=True)
    print("\nDone. Rig with:\n    python scripts/autorig.py model.glb rigged.glb --class humanoid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
