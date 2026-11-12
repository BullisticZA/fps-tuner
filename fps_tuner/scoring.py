"""Rough GPU and CPU power ratings.

GPU "power" is a relative raster-performance index (RTX 4090 = 100).
The values are approximate and only used to pick sensible settings - they are not benchmarks.
"""

from __future__ import annotations

import re

from .hardware import GPU, Hardware

# Order matters: more specific patterns (e.g. "4070 ti super") must come before shorter ones ("4070").
GPU_POWER: list[tuple[str, int]] = [
    # NVIDIA RTX 50
    (r"rtx\s*5090", 128), (r"rtx\s*5080", 84), (r"rtx\s*5070\s*ti", 72), (r"rtx\s*5070", 62),
    (r"rtx\s*5060\s*ti", 50), (r"rtx\s*5060", 45),
    # NVIDIA RTX 40
    (r"rtx\s*4090", 100), (r"rtx\s*4080", 78), (r"rtx\s*4070\s*ti", 64), (r"rtx\s*4070", 56),
    (r"rtx\s*4060\s*ti", 46), (r"rtx\s*4060", 40), (r"rtx\s*4050", 30),
    # NVIDIA RTX 30
    (r"rtx\s*3090", 62), (r"rtx\s*3080\s*ti", 60), (r"rtx\s*3080", 56), (r"rtx\s*3070\s*ti", 48),
    (r"rtx\s*3070", 46), (r"rtx\s*3060\s*ti", 40), (r"rtx\s*3060", 32), (r"rtx\s*3050", 22),
    # NVIDIA RTX 20 / GTX
    (r"rtx\s*2080\s*ti", 44), (r"rtx\s*2080", 38), (r"rtx\s*2070", 32), (r"rtx\s*2060", 26),
    (r"gtx\s*1660", 20), (r"gtx\s*1650", 14), (r"gtx\s*1080\s*ti", 34), (r"gtx\s*1080", 28),
    (r"gtx\s*1070", 24), (r"gtx\s*1060", 16), (r"gtx\s*1050", 9), (r"gtx\s*9[78]0", 14),
    # AMD
    (r"rx\s*9070\s*xt", 70), (r"rx\s*9070", 62), (r"rx\s*9060\s*xt", 44),
    (r"rx\s*7900\s*xtx", 80), (r"rx\s*7900\s*xt", 68), (r"rx\s*7900\s*gre", 58), (r"rx\s*7800\s*xt", 56),
    (r"rx\s*7700\s*xt", 48), (r"rx\s*7600", 34),
    (r"rx\s*69[05]0\s*xt", 58), (r"rx\s*6800\s*xt", 54), (r"rx\s*6800", 48), (r"rx\s*67[05]0\s*xt", 40),
    (r"rx\s*6650\s*xt", 34), (r"rx\s*6600\s*xt", 32), (r"rx\s*6600", 28), (r"rx\s*65\d0", 12),
    (r"rx\s*5700\s*xt", 30), (r"rx\s*5700", 27), (r"rx\s*5600", 22), (r"rx\s*5500", 15),
    (r"rx\s*590", 15), (r"rx\s*580", 14), (r"rx\s*570", 12),
    # Intel Arc
    (r"arc\s*b580", 38), (r"arc\s*b570", 33), (r"arc\s*a770", 34), (r"arc\s*a750", 30), (r"arc\s*a580", 26),
    (r"arc\s*a380", 12),
    # Integrated graphics
    (r"radeon\s*890m", 15), (r"radeon\s*880m", 13), (r"radeon\s*780m", 12), (r"radeon\s*760m", 10),
    (r"radeon\s*680m", 10), (r"arc\s*(140|130)v", 12), (r"intel.*arc(?!\s*[ab]\d)", 9),
    (r"iris\s*xe", 6), (r"vega\s*\d+", 4), (r"radeon.*graphics", 5), (r"uhd", 3), (r"hd graphics", 2),
]

RT_CAPABLE = re.compile(r"rtx|rx\s*(7\d{3}|9\d{3})|arc\s*[ab]\d", re.I)


def gpu_power(gpu: GPU | None) -> tuple[int, bool]:
    """Returns (power index, matched_known_model)."""
    if gpu is None:
        return 3, False
    # Strip trademark noise: "Intel(R) Arc(TM) B580" -> "intel arc b580"
    name = re.sub(r"\((r|tm|c)\)|[™®©]", "", gpu.name.lower())
    name = re.sub(r"\s+", " ", name)
    power, known = None, False
    for pattern, value in GPU_POWER:
        if re.search(pattern, name):
            power, known = value, True
            break
    if power is None:
        # Unknown model: fall back to a VRAM-based guess.
        vram = gpu.vram_gb or 0
        power = 50 if vram >= 16 else 40 if vram >= 12 else 28 if vram >= 8 else 18 if vram >= 6 else 10 if vram >= 4 else 5
    if "laptop" in name or "mobile" in name or re.search(r"\b\d{4}m\b", name) and gpu.vendor == "nvidia":
        power = round(power * 0.7)  # laptop chips run at lower power limits
    return power, known


def cpu_factor(hw: Hardware) -> float:
    """0.4-1.0 multiplier: how much the CPU holds the GPU back in CPU-heavy scenes."""
    t = hw.cpu_threads
    if t >= 16:
        return 1.0
    if t >= 12:
        return 0.95
    if t >= 8:
        return 0.85
    if t >= 6:
        return 0.75
    if t >= 4:
        return 0.55
    return 0.4


def supports_ray_tracing(gpu: GPU | None) -> bool:
    return bool(gpu and RT_CAPABLE.search(gpu.name))


def best_upscaler(gpu: GPU | None, supported: list[str]) -> str | None:
    """Pick the best upscaler this GPU can use that the game supports."""
    if not supported or gpu is None:
        return None
    name = gpu.name.lower()
    if "DLSS" in supported and gpu.vendor == "nvidia" and "rtx" in name:
        return "DLSS"
    if "XeSS" in supported and gpu.vendor == "intel" and "arc" in name:
        return "XeSS"
    if "FSR" in supported:
        return "FSR"
    if "XeSS" in supported:
        return "XeSS"
    return None
