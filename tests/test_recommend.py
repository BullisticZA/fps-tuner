import pytest

from fps_tuner.games import load_profiles, match_profile
from fps_tuner.hardware import GPU, Hardware
from fps_tuner.recommend import PRESETS, recommend
from fps_tuner.scoring import best_upscaler, gpu_power, supports_ray_tracing

PROFILES = load_profiles()


def rig(gpu: str, vram: float | None, threads: int = 16, ram: float = 32, res=(1920, 1080), hz=144) -> Hardware:
    return Hardware("Windows 11", "Test CPU", threads // 2, threads, ram, [GPU(gpu, vram)], res, hz)


def rec(hw, game):
    return recommend(hw, match_profile(game, PROFILES))


def rank(preset: str) -> int:
    return PRESETS.index(preset)


# ---------------------------------------------------------------- GPU scoring

@pytest.mark.parametrize("name, expected", [
    ("NVIDIA GeForce RTX 4070 Ti SUPER", 64),
    ("NVIDIA GeForce RTX 4070", 56),
    ("AMD Radeon RX 7900 XTX", 80),
    ("AMD Radeon RX 7900 XT", 68),
    ("Intel(R) Arc(TM) B580 Graphics", 38),
    ("NVIDIA GeForce GTX 1650", 14),
])
def test_known_gpus_match_most_specific_model(name, expected):
    assert gpu_power(GPU(name, 8)) == (expected, True)


def test_laptop_gpu_is_derated():
    desktop, _ = gpu_power(GPU("NVIDIA GeForce RTX 4060", 8))
    laptop, _ = gpu_power(GPU("NVIDIA GeForce RTX 4060 Laptop GPU", 8))
    assert laptop < desktop


def test_unknown_gpu_falls_back_to_vram():
    power, known = gpu_power(GPU("Mystery Graphics 9000", 16))
    assert not known and power == 50


def test_upscaler_choice_follows_vendor():
    all_three = ["DLSS", "FSR", "XeSS"]
    assert best_upscaler(GPU("NVIDIA GeForce RTX 3060", 12), all_three) == "DLSS"
    assert best_upscaler(GPU("NVIDIA GeForce GTX 1660", 6), all_three) == "FSR"   # no tensor cores
    assert best_upscaler(GPU("AMD Radeon RX 6600", 8), all_three) == "FSR"
    assert best_upscaler(GPU("Intel Arc A770", 16), all_three) == "XeSS"
    assert best_upscaler(GPU("NVIDIA GeForce RTX 3060", 12), []) is None


def test_ray_tracing_capability():
    assert supports_ray_tracing(GPU("NVIDIA GeForce RTX 2060", 6))
    assert supports_ray_tracing(GPU("AMD Radeon RX 7800 XT", 16))
    assert not supports_ray_tracing(GPU("NVIDIA GeForce GTX 1080 Ti", 11))


def test_integrated_gpu_is_ignored_when_discrete_present():
    hw = Hardware("Windows 11", "cpu", 8, 16, 32,
                  [GPU("Intel(R) UHD Graphics 770", 1), GPU("NVIDIA GeForce RTX 3070", 8)])
    assert hw.gpu.name == "NVIDIA GeForce RTX 3070"


# ---------------------------------------------------------------- Recommendations

def test_faster_gpu_never_gets_worse_settings():
    order = ["NVIDIA GeForce GTX 1650", "NVIDIA GeForce RTX 3060", "NVIDIA GeForce RTX 4070", "NVIDIA GeForce RTX 4090"]
    for game in ("Cyberpunk 2077", "Red Dead Redemption 2", "Counter-Strike 2", "Some Unknown Game"):
        ranks = [rank(rec(rig(g, 24), game).preset) for g in order]
        assert ranks == sorted(ranks), f"{game}: {ranks}"


def test_higher_resolution_never_gets_better_settings():
    for game in ("Cyberpunk 2077", "Black Myth: Wukong"):
        low = rec(rig("NVIDIA GeForce RTX 4070", 12, res=(1920, 1080)), game)
        high = rec(rig("NVIDIA GeForce RTX 4070", 12, res=(3840, 2160)), game)
        assert rank(high.preset) <= rank(low.preset) or high.upscale_mode is not None


def test_realistic_midrange_story_game():
    r = rec(rig("NVIDIA GeForce RTX 4070", 12, res=(2560, 1440), hz=165), "Cyberpunk 2077")
    assert r.preset == "High" and r.upscaler == "DLSS" and r.upscale_mode == "Quality"
    assert r.target_fps == 80 and r.estimated_fps >= 76


def test_low_end_needs_upscaling():
    r = rec(rig("NVIDIA GeForce GTX 1650", 4, threads=8, ram=8, hz=60), "Cyberpunk 2077")
    assert r.preset == "Low" and r.upscaler == "FSR"
    assert any("RAM" in t for t in r.tips) and any("VRAM" in t for t in r.tips)


def test_competitive_targets_high_fps_and_avoids_upscaling():
    r = rec(rig("NVIDIA GeForce RTX 3060", 12, hz=60), "Counter-Strike 2")
    assert r.target_fps == 120            # at least 120 even on a 60 Hz screen
    assert r.upscaler is None
    assert r.settings["Motion blur"] == "Off" and "Low-latency mode" in r.settings


def test_fps_cap_is_respected():
    r = rec(rig("NVIDIA GeForce RTX 4090", 24, hz=240), "ELDEN RING")
    assert r.target_fps == 60 and r.estimated_fps <= 60 and r.preset == "Ultra"


def test_textures_follow_vram_not_gpu_speed():
    small = rec(rig("NVIDIA GeForce RTX 4060 Ti", 8), "Indiana Jones and the Great Circle")
    big = rec(rig("NVIDIA GeForce RTX 4060 Ti", 16), "Indiana Jones and the Great Circle")
    assert rank(small.settings["Texture quality"]) < rank(big.settings["Texture quality"])


def test_cpu_bottleneck_is_reported():
    r = rec(rig("NVIDIA GeForce RTX 4080", 16, threads=4), "Baldur's Gate 3")
    assert r.bottleneck == "CPU"


def test_unknown_game_uses_generic_profile():
    r = rec(rig("NVIDIA GeForce RTX 3070", 8), "Totally New Indie Game")
    assert not r.profile_known and r.confidence == "medium"


def test_no_gpu_detected_still_returns_settings():
    hw = Hardware("Windows 11", "cpu", 4, 8, 16, [], None, None)
    r = recommend(hw, match_profile("Stardew Valley", PROFILES))
    assert r.preset in PRESETS and r.resolution == "1920x1080"


def test_competitive_at_1440p_prefers_medium_upscaled_over_low_native():
    r = rec(rig("NVIDIA GeForce RTX 4070", 12, res=(2560, 1440), hz=165), "Marvel Rivals")
    assert (r.preset, r.upscale_mode) == ("Medium", "Quality")
