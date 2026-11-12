"""Turns hardware + game profile into concrete graphics settings and an FPS estimate."""

from __future__ import annotations

from dataclasses import dataclass, field

from .games import GameProfile
from .hardware import Hardware
from .scoring import best_upscaler, cpu_factor, gpu_power, supports_ray_tracing

PRESETS = ["Low", "Medium", "High", "Ultra"]
# FPS multiplier of each preset relative to High.
PRESET_SPEED = {"Low": 1.8, "Medium": 1.35, "High": 1.0, "Ultra": 0.75}
UPSCALE_SPEED = {None: 1.0, "Quality": 1.3, "Balanced": 1.45, "Performance": 1.65}


@dataclass
class Recommendation:
    game: str
    preset: str
    target_fps: int
    estimated_fps: int
    resolution: str
    upscaler: str | None
    upscale_mode: str | None
    frame_generation: bool
    ray_tracing: bool
    settings: dict[str, str] = field(default_factory=dict)
    tips: list[str] = field(default_factory=list)
    bottleneck: str = "GPU"
    confidence: str = "medium"
    profile_known: bool = True

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _step(preset: str, delta: int) -> str:
    i = max(0, min(len(PRESETS) - 1, PRESETS.index(preset) + delta))
    return PRESETS[i]


def recommend(hw: Hardware, game: GameProfile,
              resolution: tuple[int, int] | None = None, refresh_hz: int | None = None) -> Recommendation:
    width, height = resolution or hw.resolution or (1920, 1080)
    refresh = refresh_hz or hw.refresh_hz or 60
    pixels = (width * height) / (1920 * 1080)
    # FPS does not drop linearly with pixel count (geometry and CPU work don't scale with resolution).
    pixel_cost = pixels ** 0.75

    gpu = hw.gpu
    power, known_gpu = gpu_power(gpu)
    cpu = cpu_factor(hw)
    effective = power * (cpu if game.cpu_heavy else (1 + cpu) / 2)

    # What frame rate is worth aiming for?
    #  - Competitive games: match the refresh rate, and at least 120 for lower input lag.
    #  - Story games: a smooth 60 (80 on high-refresh screens) with the best visuals.
    competitive = game.style == "competitive"
    if competitive:
        target = min(max(refresh, 120), 240)
    else:
        target = 60 if refresh <= 75 else 80
    if game.fps_cap:
        target = min(target, game.fps_cap)
    target = max(30, target)

    def fps_at(preset: str, mode: str | None) -> float:
        # Calibration: a GPU whose power equals the game's demand runs ~100 FPS at High, 1080p.
        return 100 * effective / (game.demand * pixel_cost) * PRESET_SPEED[preset] * UPSCALE_SPEED[mode]

    # Walk down a ladder ordered by image quality and take the first rung that reaches the target
    # (within 5%). Story games accept Quality upscaling before dropping a preset; competitive games
    # prefer native sharpness and only upscale as a last resort.
    upscaler_name = best_upscaler(gpu, game.upscalers)
    if competitive:
        ladder = [("Ultra", None), ("High", None), ("Medium", None)]
        if pixels > 1.5:
            ladder.append(("Medium", "Quality"))  # at 1440p+ this beats Low native for clarity
        ladder += [("Low", None), ("Low", "Quality"), ("Low", "Balanced"), ("Low", "Performance")]
    else:
        ladder = [("Ultra", None), ("High", None), ("High", "Quality"), ("Medium", None), ("Medium", "Quality"),
                  ("Medium", "Balanced"), ("Low", None), ("Low", "Balanced"), ("Low", "Performance")]
        if pixels > 1.5:
            # At 1440p+ Quality upscaling is nearly indistinguishable from native: allow Ultra + Quality first.
            ladder.insert(1, ("Ultra", "Quality"))
    if not upscaler_name:
        ladder = [rung for rung in ladder if rung[1] is None]

    preset, mode = ladder[-1]
    for p, m in ladder:
        if fps_at(p, m) >= target * 0.95:
            preset, mode = p, m
            break
    upscaler = upscaler_name if mode else None

    headroom = fps_at(preset, mode) / target
    ray_tracing = bool(game.rt_heavy and supports_ray_tracing(gpu) and preset == "Ultra" and headroom >= 1.3
                       and not competitive)
    # Frame generation helps when the base frame rate is decent (40+) but still short of the target.
    frame_gen = bool(game.frame_gen and not competitive and upscaler is not None and not game.fps_cap
                     and 40 <= fps_at(preset, mode) < target * 0.95)
    estimated = fps_at(preset, mode) * (0.7 if ray_tracing else 1.0)
    if game.fps_cap:
        estimated = min(estimated, game.fps_cap)

    # Textures depend on VRAM, not GPU speed.
    vram = gpu.vram_gb if gpu and gpu.vram_gb else None
    if vram is None:
        textures = preset
    elif vram >= game.vram + 4:
        textures = "Ultra"
    elif vram >= game.vram:
        textures = "High"
    elif vram >= game.vram - 2:
        textures = "Medium"
    else:
        textures = "Low"

    tight = headroom < 1.25
    settings = {
        "Overall preset": preset,
        "Resolution": f"{width}x{height}",
        "Texture quality": textures,
        "Shadows": _step(preset, -1) if tight else preset,
        "Volumetrics / fog": _step(preset, -1) if tight or competitive else preset,
        "Ambient occlusion": "Off" if competitive and tight else _step(preset, -1) if tight else preset,
        "Anti-aliasing": "Handled by upscaler" if mode else ("TAA" if not competitive else "Low / FXAA (clearer at speed)"),
        "Motion blur": "Off",
        "Depth of field": "Off" if competitive else "On",
        "Upscaling": f"{upscaler} {mode}" if mode and upscaler else "Off (native)",
        "Ray tracing": "On" if ray_tracing else "Off",
        "Frame generation": "On" if frame_gen else "Off",
        "V-Sync": "Off - use your monitor's variable refresh (G-Sync/FreeSync) instead",
        "FPS limit": f"{refresh - 3} (just under your refresh rate, keeps VRR active)" if refresh > 60 else "60",
    }
    if competitive:
        settings["Low-latency mode"] = "On (NVIDIA Reflex / AMD Anti-Lag if available)"

    bottleneck = "CPU" if game.cpu_heavy and cpu < 0.8 else "GPU"
    tips = list(game.tips)
    if bottleneck == "CPU":
        tips.insert(0, f"Your CPU ({hw.cpu_threads} threads) will limit FPS in busy scenes - close background apps.")
    if hw.ram_gb and hw.ram_gb < 16 and game.demand >= 30:
        tips.insert(0, f"{hw.ram_gb:.0f} GB RAM is low for this game - 16 GB or more avoids stutter.")
    if vram is not None and vram < game.vram:
        tips.insert(0, f"Your GPU has {vram:g} GB VRAM; this game wants {game.vram} GB for high textures - keep textures at {textures}.")
    if estimated < 30:
        tips.insert(0, "This PC is below the game's comfortable minimum - expect low FPS even on these settings.")

    confidence = "high" if known_gpu and game.known else "medium" if known_gpu or game.known else "low"

    return Recommendation(
        game=game.name, preset=preset, target_fps=int(target), estimated_fps=int(estimated),
        resolution=f"{width}x{height}", upscaler=upscaler, upscale_mode=mode,
        frame_generation=frame_gen, ray_tracing=ray_tracing, settings=settings, tips=tips,
        bottleneck=bottleneck, confidence=confidence, profile_known=game.known,
    )
