#!/usr/bin/env python3
"""Image-to-3D Lab command-line entry point."""

# This must be set before torch/SF3D is imported.
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# Caches onto the data drive (if one is set) before any tool reads its cache variable.
from image_to_3dlab.data_root import apply_env

apply_env()

from image_to_3dlab.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
