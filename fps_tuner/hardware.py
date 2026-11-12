"""Detects the hardware that matters for game performance: GPU, CPU, RAM and display."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field

import psutil


@dataclass
class GPU:
    name: str
    vram_gb: float | None = None

    @property
    def vendor(self) -> str:
        n = self.name.lower()
        if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx", "quadro")):
            return "nvidia"
        if any(k in n for k in ("amd", "radeon", " rx ")) or n.startswith("rx "):
            return "amd"
        if any(k in n for k in ("intel", "arc ", "iris", "uhd")):
            return "intel"
        return "unknown"

    @property
    def integrated(self) -> bool:
        n = self.name.lower()
        if re.search(r"\b(uhd|iris|hd graphics)\b", n):
            return True
        # AMD integrated graphics: "Radeon Graphics", "Radeon 780M", "Radeon Vega 8"
        if self.vendor == "amd" and (re.search(r"radeon\s*\(tm\)\s*graphics|radeon graphics|\d{3}m\b|vega \d", n)):
            return True
        return False


@dataclass
class Hardware:
    os: str
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_gb: float
    gpus: list[GPU] = field(default_factory=list)
    resolution: tuple[int, int] | None = None
    refresh_hz: int | None = None

    @property
    def gpu(self) -> GPU | None:
        """The GPU games will use: a discrete card if there is one, else the biggest."""
        if not self.gpus:
            return None
        discrete = [g for g in self.gpus if not g.integrated]
        pool = discrete or self.gpus
        return max(pool, key=lambda g: g.vram_gb or 0)

    def to_dict(self) -> dict:
        gpu = self.gpu
        return {
            "os": self.os,
            "cpu": self.cpu_name,
            "cpu_cores": self.cpu_cores,
            "cpu_threads": self.cpu_threads,
            "ram_gb": self.ram_gb,
            "gpu": gpu.name if gpu else None,
            "vram_gb": gpu.vram_gb if gpu else None,
            "all_gpus": [g.name for g in self.gpus],
            "resolution": f"{self.resolution[0]}x{self.resolution[1]}" if self.resolution else None,
            "refresh_hz": self.refresh_hz,
        }


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_name() -> str:
    if platform.system() == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    if os.path.exists("/proc/cpuinfo"):
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    if platform.system() == "Darwin":
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if name:
            return name
    return platform.processor() or "Unknown CPU"


def _nvidia_smi() -> list[GPU]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].replace(".", "").isdigit():
            gpus.append(GPU(parts[0], round(float(parts[1]) / 1024, 1)))
    return gpus


def _windows_vram_from_registry() -> dict[str, float]:
    """Win32_VideoController caps VRAM at 4 GB; the driver registry key has the real size."""
    sizes: dict[str, float] = {}
    try:
        import winreg

        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                key = winreg.OpenKey(root, sub)
                desc = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                size = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                sizes[desc] = round(int(size) / 1024**3, 1)
            except (OSError, ValueError, TypeError):
                continue
    except (ImportError, OSError):
        pass
    return sizes


def _windows_gpus_and_display() -> tuple[list[GPU], tuple[int, int] | None, int | None]:
    ps = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate | "
        "ConvertTo-Json -Compress"
    )
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    try:
        rows = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]

    vram = _windows_vram_from_registry()
    gpus, resolution, refresh = [], None, None
    for row in rows:
        name = (row.get("Name") or "").strip()
        if not name or "basic display" in name.lower() or "remote" in name.lower():
            continue
        size = vram.get(name)
        if size is None and row.get("AdapterRAM"):
            size = round(int(row["AdapterRAM"]) / 1024**3, 1)
        gpus.append(GPU(name, size))
        if row.get("CurrentHorizontalResolution") and resolution is None:
            resolution = (int(row["CurrentHorizontalResolution"]), int(row["CurrentVerticalResolution"]))
            refresh = int(row.get("CurrentRefreshRate") or 0) or None
    return gpus, resolution, refresh


def _linux_gpus() -> list[GPU]:
    out = _run(["lspci", "-mm"]) if shutil.which("lspci") else ""
    gpus = []
    for line in out.splitlines():
        if re.search(r'"(VGA compatible controller|3D controller|Display controller)"', line):
            fields = re.findall(r'"([^"]*)"', line)
            if len(fields) >= 3:
                gpus.append(GPU(f"{fields[1]} {fields[2]}".strip()))
    return gpus


def _linux_display() -> tuple[tuple[int, int] | None, int | None]:
    if not shutil.which("xrandr"):
        return None, None
    for line in _run(["xrandr", "--current"]).splitlines():
        if "*" in line:
            m = re.match(r"\s*(\d+)x(\d+)\s+.*?([\d.]+)\*", line)
            if m:
                return (int(m.group(1)), int(m.group(2))), round(float(m.group(3)))
    return None, None


def detect() -> Hardware:
    """Scan this machine. Never raises: anything that can't be read is left empty."""
    system = platform.system()
    resolution, refresh = None, None

    gpus = _nvidia_smi()
    if system == "Windows":
        win_gpus, resolution, refresh = _windows_gpus_and_display()
        known = {g.name for g in gpus}
        gpus += [g for g in win_gpus if g.name not in known]
    elif system == "Linux":
        known = " ".join(g.name.lower() for g in gpus)
        gpus += [g for g in _linux_gpus() if not (g.vendor == "nvidia" and "nvidia" in known)]
        resolution, refresh = _linux_display()

    return Hardware(
        os=f"{system} {platform.release()}",
        cpu_name=_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=False) or 1,
        cpu_threads=psutil.cpu_count(logical=True) or 1,
        ram_gb=round(psutil.virtual_memory().total / 1024**3, 1),
        gpus=gpus,
        resolution=resolution,
        refresh_hz=refresh,
    )
