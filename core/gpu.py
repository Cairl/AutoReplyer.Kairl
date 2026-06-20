"""
GPU acceleration module — requires NVIDIA driver, CuPy, and CUDA Toolkit.

This module is a hard dependency: if the CUDA environment is not installed
correctly, importing this module raises an error instead of silently falling
back to CPU. Whether a specific GPU model is supported is a separate concern
that surfaces at the runtime probe below.

Rationale: the environment (driver + CuPy + CUDA Toolkit) must exist as a
prerequisite. Silent CPU fallback hides missing setups and makes GPU-only
bugs impossible to reproduce. Erroring early forces a correct environment.
"""
import ctypes
import sys

import numpy  # always needed for the Pillow→array step in monitor.py

gpu_available: bool = False       # NVIDIA CUDA GPU hardware detected
cupy_available: bool = False      # CuPy installed and usable for acceleration
gpu_info: str = "Not detected"
xp = numpy  # placeholder; replaced by cupy after all checks pass


# ── Step 1: Detect CUDA GPU hardware via nvcuda.dll — required ──
def _detect_cuda_hw() -> tuple[bool, str]:
    """Detect NVIDIA CUDA GPU via nvcuda.dll (Windows) or libcuda.so (Linux)."""
    if sys.platform == "win32":
        try:
            cuda = ctypes.WinDLL("nvcuda.dll")
            cuda.cuInit(0)
            count = ctypes.c_int()
            if cuda.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0:
                name_buf = ctypes.create_string_buffer(256)
                if cuda.cuDeviceGetName(name_buf, 256, 0) == 0:
                    return True, name_buf.value.decode("utf-8",
                                                       errors="replace").strip("\x00")
                return True, "NVIDIA GPU"
        except OSError:
            pass
    else:
        try:
            cuda = ctypes.CDLL("libcuda.so")
            cuda.cuInit(0)
            count = ctypes.c_int()
            if cuda.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0:
                name_buf = ctypes.create_string_buffer(256)
                if cuda.cuDeviceGetName(name_buf, 256, 0) == 0:
                    return True, name_buf.value.decode("utf-8",
                                                       errors="replace").strip("\x00")
                return True, "NVIDIA GPU"
        except OSError:
            pass
    return False, ""


found, name = _detect_cuda_hw()
if not found:
    raise RuntimeError(
        "未检测到 NVIDIA CUDA GPU。\n"
        "请安装 NVIDIA 驱动并确保 GPU 可用。\n"
        "WeAutoReplyer 要求完整的 CUDA 环境作为前提。"
    )
gpu_available = True
gpu_info = name

# ── Step 2: Import CuPy — required ──
# CuPy's _environment module probes for a *system-wide* CUDA install
# (via CUDA_PATH) and warns "CUDA path could not be detected" when it
# finds none. In wheel mode (cupy-cuda12x[ctk]) the CUDA libs live
# inside nvidia/* wheels with no single root, so this probe always
# returns None — but CuPy still works fine via cuda-pathfinder. The
# warning is a false alarm in this setup; silence only that specific
# message so real CuPy errors (ImportError/RuntimeError) stay visible.
import warnings
warnings.filterwarnings(
    "ignore",
    message=r"CUDA path could not be detected.*",
    category=UserWarning,
)
try:
    import cupy as cp  # type: ignore[import-untyped]
except ImportError as e:
    raise ImportError(
        "未安装 CuPy。请运行: pip install cupy-cuda12x\n"
        "WeAutoReplyer 要求完整的 CUDA 环境作为前提。"
    ) from e

# ── Step 3: Verify CuPy can actually use the GPU — required ──
# Two-stage check: cp.cuda.is_available() only probes GPU hardware/driver
# and returns True even when CuPy cannot compile kernels (which needs the
# CUDA toolkit headers / CUDA_PATH). Without the real-op probe below, the
# first array op in the monitor would raise RuntimeError and break every
# scan, so no reply would ever fire.
if not cp.cuda.is_available():
    raise RuntimeError(
        "CuPy 报告无可用 CUDA 设备。\n"
        "请检查 NVIDIA 驱动和 CUDA Toolkit 是否正确安装。\n"
        "WeAutoReplyer 要求完整的 CUDA 环境作为前提。"
    )

# Force CuPy to compile & run a tiny kernel. Catches
# "Failed to find CUDA headers" (missing CUDA_PATH) and other
# runtime-only failures that is_available() cannot detect.
try:
    _probe = cp.array([1, 2, 3], dtype=cp.int16)
    _ = float(cp.std(cp.abs(_probe - 1).astype(cp.int16)))
    del _probe
    cp.get_default_memory_pool().free_all_blocks()
except Exception as e:
    raise RuntimeError(
        f"CuPy 运行时不可用: {type(e).__name__}: {e}\n"
        "请检查 CUDA Toolkit 版本是否与 GPU 架构兼容。\n"
        "WeAutoReplyer 要求完整的 CUDA 环境作为前提。"
    ) from e

cupy_available = True
xp = cp

# Refresh GPU name from CuPy (more detailed than nvcuda.dll).
device: dict = cp.cuda.runtime.getDeviceProperties(0)
dev_name = device["name"]
if isinstance(dev_name, bytes):
    dev_name = dev_name.decode("utf-8")
gpu_info = str(dev_name)
