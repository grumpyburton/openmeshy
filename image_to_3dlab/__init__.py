"""Local image-to-3D backends for Apple Silicon."""

from image_to_3dlab.data_root import apply_env as _apply_env

__version__ = "0.3.9"

# Any script that imports the package (bootstraps, generators) downloads onto the data
# drive when one is set. Defaults only: an exported cache variable still wins.
_apply_env()
