"""Self-contained HTML report."""

from __future__ import annotations

from datetime import datetime
from html import escape

from . import __version__
from .hardware import Hardware
from .recommend import Recommendation

_PRESET_COLOR = {"Ultra": "#a78bfa", "High": "#5ee7ff", "Medium": "#fbbf24", "Low": "#f87171"}


def _card(rec: Recommendation) -> str:
    color = _PRESET_COLOR.get(rec.preset, "#5ee7ff")
    meter = min(100, round(100 * rec.estimated_fps / max(rec.target_fps, 1)))
    rows = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rec.settings.items())
    tips = "".join(f"<li>{escape(t)}</li>" for t in rec.tips)
    badges = []
    if rec.upscaler:
        badges.append(f"{rec.upscaler} {rec.upscale_mode}")
    if rec.ray_tracing:
        badges.append("Ray tracing")
    if rec.frame_generation:
        badges.append("Frame gen")
    if not rec.profile_known:
        badges.append("Generic profile")
    badge_html = "".join(f"<span class='badge'>{escape(b)}</span>" for b in badges)
    return f"""
<details class="game">
  <summary>
    <div class="title">{escape(rec.game)}</div>
    <div class="preset" style="color:{color}">{escape(rec.preset)}</div>
    <div class="fps"><b>~{rec.estimated_fps}</b> FPS <span class="muted">/ target {rec.target_fps}</span>
      <div class="meter"><i style="width:{meter}%"></i></div></div>
    <div class="badges">{badge_html}</div>
  </summary>
  <table>{rows}</table>
  {f"<ul class='tips'>{tips}</ul>" if tips else ""}
  <div class="muted small">Bottleneck: {escape(rec.bottleneck)} &middot; Confidence: {escape(rec.confidence)}</div>
</details>"""


def render(hw: Hardware, recs: list[Recommendation]) -> str:
    info = hw.to_dict()
    hw_rows = [
        ("GPU", f"{info['gpu'] or 'Not detected'}" + (f" ({info['vram_gb']:g} GB)" if info["vram_gb"] else "")),
        ("CPU", f"{info['cpu']} ({info['cpu_cores']} cores / {info['cpu_threads']} threads)"),
        ("RAM", f"{info['ram_gb']:g} GB"),
        ("Display", f"{info['resolution'] or 'Unknown'} @ {info['refresh_hz'] or '?'} Hz"),
        ("OS", info["os"]),
    ]
    hw_html = "".join(f"<div class='spec'><div class='muted'>{k}</div><div>{escape(str(v))}</div></div>" for k, v in hw_rows)
    cards = "".join(_card(r) for r in recs) or "<p class='muted'>No games found.</p>"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPS Tuner report</title>
<style>
:root{{--bg:#070b14;--card:#111c33;--line:#1f3157;--text:#e6edf7;--muted:#8fa3c4;--accent:#5ee7ff;--accent2:#8b5cf6}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% -20%,#16274a,var(--bg) 60%);color:var(--text);
font:15px/1.5 system-ui,Segoe UI,sans-serif;padding:24px 16px;min-height:100vh}}
.wrap{{max-width:980px;margin:auto}}h1{{margin:0;font-size:30px;letter-spacing:-.5px}}
h1 span{{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}}
.muted{{color:var(--muted)}}.small{{font-size:13px;margin-top:10px}}
.specs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:20px 0 28px}}
.spec{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
.game{{background:var(--card);border:1px solid var(--line);border-radius:14px;margin-bottom:10px;padding:0 16px}}
.game[open]{{box-shadow:0 0 0 1px var(--accent) inset}}
summary{{display:grid;grid-template-columns:1.6fr .6fr 1fr 1.4fr;gap:12px;align-items:center;padding:14px 0;cursor:pointer;list-style:none}}
summary::-webkit-details-marker{{display:none}}.title{{font-weight:700}}.preset{{font-weight:800;font-size:18px}}
.meter{{height:6px;background:#0a1222;border-radius:9px;overflow:hidden;margin-top:4px}}
.meter i{{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}}
.badge{{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:99px;padding:1px 9px;margin:2px 4px 2px 0;color:var(--accent)}}
table{{width:100%;border-collapse:collapse;margin:4px 0 8px}}td{{padding:7px 0;border-top:1px solid var(--line);vertical-align:top}}
td:first-child{{color:var(--muted);width:42%}}.tips{{margin:8px 0;padding-left:20px}}.tips li{{margin:3px 0}}
footer{{margin-top:30px;font-size:13px}}
@media (max-width:640px){{summary{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class="wrap">
<h1><span>FPS Tuner</span> report</h1>
<div class="muted">{stamp} &middot; {len(recs)} game(s) &middot; click a game for its settings</div>
<div class="specs">{hw_html}</div>
{cards}
<footer class="muted">FPS Tuner v{__version__}. FPS figures are estimates to guide your settings, not benchmarks.</footer>
</div></body></html>"""
