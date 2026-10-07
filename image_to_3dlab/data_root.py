"""One place for everything big: weights, caches, backend checkouts and outputs.

Models, Python environments and caches run to tens of gigabytes. On a Mac with a full
internal disk they belong on another drive, and saying so should take one setting, not
a hunt through a dozen tools' cache conventions.

The setting is the ``I2L_DATA`` environment variable, or failing that a one-line
``.i2l-data`` file at the repo root (git-ignored, written by
``scripts/relocate_data.py``). When set:

* every cache we know of (Hugging Face, rembg, torch, uv, pip, cargo/rustup) defaults
  into it -- defaults only, so a value already exported wins;
* the repo's heavy, git-ignored folders (``vendor/``, ``output/``, weights) become
  symlinks into it, so the many paths written as ``REPO / "vendor"`` keep working.

The drive should be APFS (or another filesystem with symlinks): Python environments
are made of them. On an exFAT drive, put an APFS disk image on it and point here.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import MutableMapping
from pathlib import Path

ENV_VAR = "I2L_DATA"
POINTER_FILE = ".i2l-data"
REPO = Path(__file__).resolve().parents[1]

# Repo-relative folders that are git-ignored and can grow to gigabytes.
LINKED_DIRS = (
    ".venv",
    "vendor",
    "output",
    "characters",
    "hunyuan_mlx/shape/weights",
    "hunyuan_mlx/paint/weights",
    "hunyuan_mlx/shape/outputs",
    "hunyuan_mlx/paint/outputs",
    "hunyuan_mlx/shape/.venv",
    "hunyuan_mlx/paint/.venv",
)

# Cache variable -> folder under the data root.
CACHE_DIRS = {
    "HF_HOME": "hf",
    "U2NET_HOME": "u2net",
    "TORCH_HOME": "torch",
    "UV_CACHE_DIR": "uv-cache",
    "UV_PYTHON_INSTALL_DIR": "uv-python",
    "PIP_CACHE_DIR": "pip-cache",
    "XDG_CACHE_HOME": "xdg-cache",
    "XDG_DATA_HOME": "xdg-data",
    "CARGO_HOME": "cargo",
    "RUSTUP_HOME": "rustup",
}


def data_root(environ: MutableMapping[str, str] | None = None, repo: Path = REPO) -> Path | None:
    """The configured data root, or None when everything stays in the default places."""
    environ = os.environ if environ is None else environ
    value = environ.get(ENV_VAR, "").strip()
    if not value:
        pointer = repo / POINTER_FILE
        if pointer.is_file():
            value = pointer.read_text(encoding="utf-8").strip()
    return Path(value).expanduser() if value else None


def missing_root_help(root: Path | None) -> str | None:
    """Why the configured data drive cannot be used, in words, or None when it can.

    An unplugged drive or an unmounted disk image leaves every symlink dangling, and the
    failure then surfaces far away as a missing venv or weight file.
    """
    if root is None or root.is_dir():
        return None
    return (f"The data drive {root} is not there. Plug the drive in (or attach its disk "
            f"image, e.g. `hdiutil attach /Volumes/<drive>/<name>.sparsebundle`), then try "
            f"again. Set {ENV_VAR} or edit {POINTER_FILE} to move it.")


def cache_env(root: Path) -> dict[str, str]:
    """Cache environment variables pointing into ``root``."""
    return {name: str(root / folder) for name, folder in CACHE_DIRS.items()}


def apply_env(environ: MutableMapping[str, str] | None = None, repo: Path = REPO) -> Path | None:
    """Default every cache variable into the data root. Call before importing tools.

    Returns the root, or None if none is configured. Values already set are kept, and
    ``I2L_DATA`` itself is exported so child processes agree.
    """
    environ = os.environ if environ is None else environ
    root = data_root(environ, repo)
    if root is None:
        return None
    environ.setdefault(ENV_VAR, str(root))
    for name, value in cache_env(root).items():
        environ.setdefault(name, value)
    return root


def link_plan(root: Path, repo: Path = REPO) -> list[tuple[Path, Path]]:
    """(repo path, target under the root) for every heavy folder not yet linked there."""
    plan = []
    for rel in LINKED_DIRS:
        link = repo / rel
        target = root / "repo" / rel
        if link.is_symlink() and Path(os.readlink(link)) == target:
            continue
        plan.append((link, target))
    return plan


def link_dirs(root: Path, repo: Path = REPO) -> list[tuple[Path, Path]]:
    """Move each heavy folder's contents to the root and leave a symlink behind.

    Idempotent. A folder that does not exist yet still gets its link, so the first
    download lands on the data drive. Refuses to overwrite a non-empty target.
    """
    done = []
    for link, target in link_plan(root, repo):
        target.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            old = Path(os.readlink(link))
            if old.is_dir() and any(old.iterdir()):
                raise RuntimeError(f"{link} already points at {old}; move it by hand")
            link.unlink()
        elif link.exists():
            if target.exists() and any(target.iterdir()):
                raise RuntimeError(f"{target} already has files; move {link} by hand")
            if target.exists():
                target.rmdir()
            shutil.move(str(link), str(target))
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        done.append((link, target))
    return done


def inside(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether ``path`` is one of ``roots`` or under one (no symlink resolution)."""
    path = Path(os.path.normpath(path))
    for root in roots:
        root = Path(os.path.normpath(root))
        if root == path or root in path.parents:
            return True
    return False
