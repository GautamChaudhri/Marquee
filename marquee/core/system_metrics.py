"""Host telemetry for the dashboard (frontend G1) and Projection Room.

Point-in-time CPU / RAM / disk / network / GPU readings. The frontend
accumulates sparkline history and computes disk/network rates from deltas
between successive polls — this module is stateless.

GPU metrics use NVML via ``nvidia-ml-py`` (the ``[nvidia]`` extra). The import
and init are lazy and cached; on hosts without an NVIDIA GPU (Mac, Intel, CPU)
``gpu_metrics()`` returns ``None`` and the dashboard simply hides the GPU card.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
def _cpu_model() -> str:
    """Best-effort human CPU model string."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "Unknown CPU"


def _cpu_temp() -> float | None:
    """First available CPU package temperature, or None."""
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None
    try:
        temps = sensors()
    except Exception:  # noqa: BLE001 - platform/driver dependent
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        readings = temps.get(key)
        if readings:
            return round(readings[0].current, 1)
    # Fall back to whatever the first sensor group reports.
    for readings in temps.values():
        if readings:
            return round(readings[0].current, 1)
    return None


def cpu_metrics() -> dict[str, Any]:
    freq = psutil.cpu_freq()
    try:
        load1 = psutil.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = None
    return {
        "model": _cpu_model(),
        "cores": psutil.cpu_count(logical=False),
        "threads": psutil.cpu_count(logical=True),
        # interval=None is non-blocking; the value is the load since the
        # previous call, so the first poll after startup reads ~0.
        "avg": psutil.cpu_percent(interval=None),
        "perCore": psutil.cpu_percent(interval=None, percpu=True),
        "freq": round(freq.current, 0) if freq else None,
        "load": round(load1, 2) if load1 is not None else None,
        "temp": _cpu_temp(),
    }


# ---------------------------------------------------------------------------
# Memory / disk / uptime
# ---------------------------------------------------------------------------
def ram_metrics() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {"used": vm.used, "total": vm.total, "pct": vm.percent}


def disk_metrics(path: str | Path) -> dict[str, Any]:
    try:
        usage = psutil.disk_usage(str(path))
    except OSError:
        return {"used": None, "total": None, "pct": None}
    return {"used": usage.used, "total": usage.total, "pct": usage.percent}


def disk_io() -> dict[str, Any]:
    """Cumulative (since-boot) disk read/write byte counters, host-wide.

    Cumulative, not a rate — the frontend computes bytes/sec from the delta
    between two polls, matching this module's stateless contract.
    """
    try:
        io = psutil.disk_io_counters()
    except OSError:
        return {"readBytes": None, "writeBytes": None}
    if io is None:
        return {"readBytes": None, "writeBytes": None}
    return {"readBytes": io.read_bytes, "writeBytes": io.write_bytes}


def net_io() -> dict[str, Any]:
    """Cumulative (since-boot) network byte counters, host-wide (all interfaces)."""
    try:
        io = psutil.net_io_counters()
    except OSError:
        return {"bytesSent": None, "bytesRecv": None}
    if io is None:
        return {"bytesSent": None, "bytesRecv": None}
    return {"bytesSent": io.bytes_sent, "bytesRecv": io.bytes_recv}


def _fmt_uptime(seconds: float) -> str:
    secs = int(seconds)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def uptime() -> str:
    return _fmt_uptime(time.time() - psutil.boot_time())


# ---------------------------------------------------------------------------
# GPU (NVML, optional)
# ---------------------------------------------------------------------------
_nvml: dict[str, Any] = {"init": None, "mod": None}


def _ensure_nvml() -> bool:
    """Lazily import + init NVML once; cache success/failure."""
    if _nvml["init"] is None:
        try:
            import pynvml  # provided by the nvidia-ml-py package  # noqa: PLC0415

            pynvml.nvmlInit()
            _nvml["mod"] = pynvml
            _nvml["init"] = True
        except Exception as exc:  # noqa: BLE001 - no driver/lib on this host
            logger.debug("NVML unavailable, GPU metrics disabled: %s", exc)
            _nvml["init"] = False
    return bool(_nvml["init"])


def gpu_metrics() -> dict[str, Any] | None:
    """NVIDIA GPU 0 metrics, or None when no NVML/GPU is present."""
    if not _ensure_nvml():
        return None
    pynvml = _nvml["mod"]
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode()
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        try:
            power: float | None = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
        except Exception:  # noqa: BLE001 - not all cards report power
            power = None
        try:
            enc = pynvml.nvmlDeviceGetEncoderUtilization(handle)[0]
        except Exception:  # noqa: BLE001 - encoder stats are optional
            enc = None
        return {
            "model": name,
            "util": util.gpu,
            "memUtil": util.memory,
            "vramUsed": mem.used,
            "vramTotal": mem.total,
            "temp": temp,
            "power": round(power, 1) if power is not None else None,
            "enc": enc,
        }
    except Exception as exc:  # noqa: BLE001 - degrade rather than 500
        logger.debug("NVML query failed: %s", exc)
        return None


def collect(disk_path: str | Path) -> dict[str, Any]:
    """The hardware bundle for ``GET /api/system/metrics`` (sans workers)."""
    return {
        "cpu": cpu_metrics(),
        "gpu": gpu_metrics(),
        "ram": ram_metrics(),
        "disk": {**disk_metrics(disk_path), **disk_io()},
        "net": net_io(),
        "uptime": uptime(),
    }
