"""Tell a GPU run of stable-diffusion.cpp from a CPU one, from what it prints at start-up.

The NVIDIA builds load their GPU backend as a separate library at run time. When that
library cannot reach the driver, sd-cli does not fail: it carries on with only the CPU
backend. On the RunPod 4090 that turned a 20-second picture into a many-minute one at
1,700% CPU, with the GPU idle and nothing on screen saying why. The cause there was two
missing system graphics libraries on a headless box.
"""

from __future__ import annotations

import re

NO_GPU_HELP = (
    "stable-diffusion.cpp could not reach the GPU and would run on the CPU, which "
    "takes many minutes per picture. Check that `nvidia-smi` (NVIDIA) or "
    "`vulkaninfo --summary` (AMD) lists your card. On Linux, "
    "especially a headless server or container, the usual cause is missing graphics "
    "libraries: sudo apt install libegl1 libgl1. In a container, also set "
    "NVIDIA_DRIVER_CAPABILITIES=all."
)

_GPU = re.compile(
    r"ggml_vulkan: Found [1-9]"
    r"|ggml_cuda_init: found [1-9]"
    r"|load_backend: loaded (?:Vulkan|CUDA) backend"
)
_CPU = re.compile(r"load_backend: loaded CPU backend")


def backend_of(line: str) -> str | None:
    """`gpu` or `cpu` if this line announces a backend, else None."""
    if _GPU.search(line):
        return "gpu"
    if _CPU.search(line):
        return "cpu"
    return None


def gpu_found(output: str) -> bool:
    return any(backend_of(line) == "gpu" for line in output.splitlines())


class BackendWatch:
    """Fed sd-cli's lines in order; says when the CPU came up with no GPU before it.

    The GPU backend always loads before the CPU one, so the CPU line arriving first is
    the moment to stop the run rather than let it grind.
    """

    def __init__(self) -> None:
        self.gpu = False

    def feed(self, line: str) -> bool:
        kind = backend_of(line)
        if kind == "gpu":
            self.gpu = True
        return kind == "cpu" and not self.gpu
