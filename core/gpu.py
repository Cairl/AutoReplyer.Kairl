"""
GPU detection and acceleration module.
Silently detects CUDA GPU availability and provides a unified xp namespace
that maps to cupy (GPU) or numpy (CPU fallback).
"""
import ctypes
import sys

import numpy

gpu_available: bool = False       # Hardware CUDA GPU detected
cupy_available: bool = False      # CuPy installed and usable for acceleration
gpu_info: str = "Not detected"
xp = numpy  # default to numpy (CPU fallback)


# ── Step 1: Detect CUDA GPU hardware via nvcuda.dll ──
def _detect_cuda_hw() -> bool:
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
if found:
    gpu_available = True
    gpu_info = name

# ── Step 2: Try CuPy for GPU-accelerated array ops ──
# Two-stage check: cp.cuda.is_available() only probes GPU hardware/driver
# and returns True even when CuPy cannot compile kernels (which needs the
# CUDA toolkit headers / CUDA_PATH). Without the real-op probe below, the
# first array op in the monitor would raise RuntimeError and break every
# scan, so no reply would ever fire.
gpu_fallback_reason: str = ""  # why GPU acceleration is inactive ("" = active)
if gpu_available:
    try:
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
        import cupy as cp  # type: ignore[import-untyped]
    except ImportError:
        # CuPy is an optional accelerator; a missing install must not crash
        # the app. Fall back to numpy and record the reason for the UI.
        gpu_fallback_reason = "未安装 CuPy (pip install cupy-cuda12x)"
    else:
        try:
            if not cp.cuda.is_available():
                gpu_fallback_reason = "CuPy 报告无可用 CUDA 设备"
            else:
                # Force CuPy to compile & run a tiny kernel. Catches
                # "Failed to find CUDA headers" (missing CUDA_PATH) and other
                # runtime-only failures that is_available() cannot detect.
                _probe = cp.array([1, 2, 3], dtype=cp.int16)
                _ = float(cp.std(cp.abs(_probe - 1).astype(cp.int16)))
                del _probe
                cp.get_default_memory_pool().free_all_blocks()

                cupy_available = True
                xp = cp
                # Refresh GPU name from CuPy (more detailed than nvcuda.dll).
                device: dict = cp.cuda.runtime.getDeviceProperties(0)
                dev_name = device["name"]
                if isinstance(dev_name, bytes):
                    dev_name = dev_name.decode("utf-8")
                gpu_info = str(dev_name)
        except Exception as _probe_err:
            # CuPy importable but unusable at runtime (missing CUDA toolkit
            # headers, driver/runtime mismatch, etc.). GPU acceleration is
            # best-effort: keep xp = numpy so the monitor keeps working.
            gpu_fallback_reason = f"CuPy 运行时不可用: {type(_probe_err).__name__}"
            cupy_available = False
            xp = numpy
