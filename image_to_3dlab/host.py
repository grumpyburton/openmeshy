"""What machine is this, in the vocabulary backends declare support in.

One module so the viewer and every bootstrap answer the question the same way. Three
answers matter today: an Apple Silicon Mac, a Linux/Windows box with an NVIDIA card, or
a Linux box with an AMD card (ggml's Vulkan prebuilts run there). Everything else is
"other", and a backend that does not list it will not offer a download.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

APPLE = "apple-silicon"
NVIDIA = "nvidia"
AMD = "amd"
OTHER = "other"


def os_family(sys_platform: str | None = None) -> str:
    name = sys_platform or sys.platform
    if name == "darwin":
        return "macos"
    if name.startswith("linux"):
        return "linux"
    if name in ("win32", "cygwin"):
        return "windows"
    return OTHER


def _smi(args: list[str], which: Callable, run: Callable) -> str | None:
    """`nvidia-smi <args>` stdout, or None if it is missing, fails or hangs."""
    smi = which("nvidia-smi")
    if not smi:
        return None
    try:
        result = run([smi, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or "") if result.returncode == 0 else None


def has_nvidia_gpu(which: Callable = shutil.which, run: Callable = subprocess.run) -> bool:
    """True when `nvidia-smi` lists at least one GPU.

    The driver ships `nvidia-smi`, so it is the cheapest honest test: no CUDA toolkit, no
    torch. Its presence alone is not enough (a container can have the tool but no card
    mounted), so it has to list a GPU too.
    """
    return "GPU" in (_smi(["-L"], which, run) or "")


def driver_cuda_version(which: Callable = shutil.which,
                        run: Callable = subprocess.run) -> tuple[int, int] | None:
    """The newest CUDA the installed driver supports, from `nvidia-smi`'s header.

    This is the driver's ceiling, not an installed toolkit. A binary compiled with a newer
    CUDA than this fails at its first kernel, not at load time. Driver 610 renamed the
    field from "CUDA Version" to "CUDA UMD Version", so both spellings are accepted.
    """
    match = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", _smi([], which, run) or "")
    return (int(match[1]), int(match[2])) if match else None


def compute_capability(which: Callable = shutil.which,
                       run: Callable = subprocess.run) -> str | None:
    """The first card's compute capability as CMake spells it (`8.9` becomes `89`)."""
    out = _smi(["--query-gpu=compute_cap", "--format=csv,noheader"], which, run) or ""
    match = re.match(r"\s*(\d+)\.(\d+)", out)
    return f"{match[1]}{match[2]}" if match else None


DRM = Path("/sys/class/drm")
AMD_VENDOR = "0x1002"


def has_amd_gpu(drm: Path = DRM) -> bool:
    """True when the Linux kernel exposes an AMD graphics device.

    Read from sysfs rather than a tool: `rocminfo` needs ROCm installed and `vulkaninfo`
    the Vulkan SDK, but every DRM driver writes its PCI vendor id here, and 0x1002 is
    AMD. An integrated Radeon counts too; which device ggml's Vulkan backend then uses is
    its own choice (`GGML_VK_VISIBLE_DEVICES` overrides it).
    """
    try:
        vendors = list(drm.glob("card*/device/vendor"))
    except OSError:
        return False
    for vendor in vendors:
        try:
            if vendor.read_text().strip().lower() == AMD_VENDOR:
                return True
        except OSError:
            continue
    return False


@lru_cache(maxsize=1)
def _cached_nvidia() -> bool:
    return has_nvidia_gpu()


# The shared libraries upstream's HIP build of pixal3d.cpp links against (ldd, 2026-10-08).
# All three live in ROCm's lib directory; the runtime alone is not enough.
ROCM_LIBS = ("libamdhip64.so", "libhipblas.so", "librocblas.so")


def rocm_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("ROCM_PATH") or env.get("ROCM_HOME") or "/opt/rocm")


def has_rocm(root: Path | None = None) -> bool:
    """True when a ROCm install with HIP and the BLAS libraries is present.

    Checked by files rather than by running `rocminfo`: the question is whether a HIP
    binary can load here, and that is decided by the libraries, not by a tool that may
    or may not be on PATH.
    """
    lib = (root or rocm_root()) / "lib"
    try:
        return all(any(lib.glob(f"{name}*")) for name in ROCM_LIBS)
    except OSError:
        return False


@lru_cache(maxsize=1)
def _cached_amd() -> bool:
    return has_amd_gpu()


def host_platform(sys_platform: str | None = None, machine: str | None = None,
                  nvidia: Callable[[], bool] = _cached_nvidia,
                  amd: Callable[[], bool] = _cached_amd) -> str:
    """APPLE, NVIDIA, AMD or OTHER.

    A Mac is decided from `sys.platform` and the CPU alone. Anything else asks the driver,
    once per process: NVIDIA first, then AMD. AMD is Linux-only for now, because the
    check reads sysfs and the Vulkan prebuilts were only tried there.
    """
    family = os_family(sys_platform)
    if family == "macos":
        return APPLE if (machine or platform.machine()) == "arm64" else OTHER
    if family in ("linux", "windows") and nvidia():
        return NVIDIA
    if family == "linux" and amd():
        return AMD
    return OTHER


def find_nvcc() -> str | None:
    """The CUDA compiler: on PATH, or where the toolkit installs it by default. Its
    absence from PATH is normal, so the default location is worth checking."""
    found = shutil.which("nvcc")
    if found:
        return found
    default = Path("/usr/local/cuda/bin/nvcc")
    return str(default) if default.exists() else None


def nvcc_cuda_version(nvcc: str | None,
                      run: Callable = subprocess.run) -> tuple[int, int] | None:
    """The CUDA version a toolkit compiles for, from `nvcc --version`'s release line.

    Worth comparing with `driver_cuda_version`: kernels built by a newer toolkit than the
    driver supports fail at their first launch.
    """
    if not nvcc:
        return None
    try:
        result = run([nvcc, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"release\s+(\d+)\.(\d+)", result.stdout or "")
    return (int(match[1]), int(match[2])) if match else None


CGROUP = Path("/sys/fs/cgroup")
# cgroup v1 spells "no limit" as a page-rounded 2**63; anything past this is not a limit.
_NO_LIMIT = 1 << 60


def cgroup_cpus(root: Path = CGROUP) -> int | None:
    """The container's CPU quota, rounded down, or None when there is none.

    Containers report the host's CPU count (96 on a RunPod 4090) while being allowed a
    fraction of it; the quota is the real number.
    """
    try:
        quota, period = (root / "cpu.max").read_text().split()[:2]
    except (OSError, ValueError):
        return None
    if quota == "max":
        return None
    return max(1, int(quota) // int(period))


def cgroup_memory(root: Path = CGROUP) -> int | None:
    """The container's memory limit in bytes (cgroup v2, then v1), or None."""
    for path in (root / "memory.max", root / "memory" / "memory.limit_in_bytes"):
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if text.isdigit() and int(text) < _NO_LIMIT:
            return int(text)
        return None
    return None


def total_memory() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def patient_downloads(env: dict[str, str]) -> dict[str, str]:
    """`env` with uv told to wait for slow downloads instead of giving up.

    uv gives a read 30 seconds by default; PyTorch's 700 MB cuDNN wheel timed out on a slow
    connection and killed a whole setup. A value the user set themselves is kept.
    """
    env = dict(env)
    env.setdefault("UV_HTTP_TIMEOUT", "300")
    env.setdefault("UV_HTTP_RETRIES", "5")
    return env


def build_jobs(cpus: int | None = None, memory_bytes: int | None = None,
               per_job_bytes: int = 3 * 1024 ** 3) -> int:
    """How many compile jobs to run at once: capped by CPUs *and* by memory.

    A CUDA compile job can take a few GB, so a bare `-j` on a machine with many cores and
    modest RAM gets its compilers killed. Called with no arguments, it measures this
    machine, preferring the container's limits over the host's.
    """
    if cpus is None and memory_bytes is None:
        cpus = cgroup_cpus() or os.cpu_count()
        memory_bytes = cgroup_memory() or total_memory()
    by_memory = memory_bytes // per_job_bytes if memory_bytes else None
    limits = [n for n in (cpus, by_memory) if n is not None]
    return max(1, min(limits, default=1))


def executable(directory: Path, name: str, family: str | None = None) -> Path:
    """`directory/name`, spelled the way this OS spells a program."""
    suffix = ".exe" if (family or os_family()) == "windows" else ""
    return directory / f"{name}{suffix}"


def build_target(platform_id: str | None = None, family: str | None = None) -> str | None:
    """Which prebuilt a bootstrap should fetch here: `macos-arm64`, `linux-nvidia`,
    `windows-nvidia`, `linux-amd`, or None when nothing fits."""
    platform_id = platform_id or host_platform()
    family = family or os_family()
    if platform_id == APPLE:
        return "macos-arm64"
    if platform_id == NVIDIA and family in ("windows", "linux"):
        return f"{family}-nvidia"
    if platform_id == AMD and family == "linux":
        return "linux-amd"
    return None


# PyTorch's own index, newest CUDA first. PyPI only carries CPU-only torch for Windows, so
# an install there needs one of these. Each needs a driver at least that new; 12.8 is also
# the first build that knows RTX 50-series cards.
TORCH_CUDA_BUILDS: tuple[tuple[tuple[int, int], str], ...] = (
    ((13, 0), "cu130"),
    ((12, 8), "cu128"),
)
TORCH_INDEX = "https://download.pytorch.org/whl/"


def torch_cuda_index(driver: tuple[int, int] | None) -> str | None:
    """The PyTorch index whose CUDA build this driver can run, or None."""
    if driver is None:
        return None
    for minimum, tag in TORCH_CUDA_BUILDS:
        if driver >= minimum:
            return TORCH_INDEX + tag
    return None


def _installed_torch_cuda() -> str | None:
    """The CUDA version the installed torch was built with; None if CPU-only or missing."""
    try:
        import torch
    except Exception:  # noqa: BLE001 - any failure means "no usable CUDA torch"
        return None
    return torch.version.cuda


def main(argv: list[str] | None = None, *, which: Callable = shutil.which,
         run: Callable = subprocess.run,
         torch_cuda: Callable[[], str | None] = _installed_torch_cuda) -> int:
    """Small questions for the installers, answered without them parsing anything.

    `torch-index`: print the PyTorch CUDA index for this driver (empty if none fits).
    `torch-has-cuda`: exit 0 if the installed torch has CUDA, 1 if not.
    """
    command = (argv if argv is not None else sys.argv[1:])[:1]
    if command == ["torch-index"]:
        print(torch_cuda_index(driver_cuda_version(which, run)) or "")
        return 0
    if command == ["torch-has-cuda"]:
        return 0 if torch_cuda() else 1
    print("usage: python -m image_to_3dlab.host {torch-index|torch-has-cuda}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
