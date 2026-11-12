"""Command-line interface with an animated scan and a colourful summary."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import webbrowser
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, report
from .games import InstalledGame, find_installed_games, load_profiles, match_profile
from .hardware import GPU, Hardware, detect
from .recommend import Recommendation, recommend
from .scoring import gpu_power

PRESET_STYLE = {"Ultra": "bold magenta", "High": "bold cyan", "Medium": "bold yellow", "Low": "bold red"}

BANNER = r"""
 ███████╗██████╗ ███████╗    ████████╗██╗   ██╗███╗   ██╗███████╗██████╗
 ██╔════╝██╔══██╗██╔════╝    ╚══██╔══╝██║   ██║████╗  ██║██╔════╝██╔══██╗
 █████╗  ██████╔╝███████╗       ██║   ██║   ██║██╔██╗ ██║█████╗  ██████╔╝
 ██╔══╝  ██╔═══╝ ╚════██║       ██║   ██║   ██║██║╚██╗██║██╔══╝  ██╔══██╗
 ██║     ██║     ███████║       ██║   ╚██████╔╝██║ ╚████║███████╗██║  ██║
 ╚═╝     ╚═╝     ╚══════╝       ╚═╝    ╚═════╝ ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝"""


def _gradient_banner() -> Text:
    colors = ["#5ee7ff", "#4fc3ff", "#3b82f6", "#5b6cf6", "#7a63f6", "#8b5cf6"]
    text = Text()
    for i, line in enumerate(BANNER.strip("\n").splitlines()):
        text.append(line + "\n", style=f"bold {colors[i % len(colors)]}")
    text.append(f"  Smooth frames, zero guesswork  ·  v{__version__}", style="dim")
    return text


def _parse_resolution(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{3,5})\s*[xX×]\s*(\d{3,5})", value.strip())
    if not m:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT, e.g. 2560x1440")
    return int(m.group(1)), int(m.group(2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fps-tuner", description="Scan your PC and games, then get the best graphics settings for smooth FPS.")
    p.add_argument("-g", "--game", action="append", metavar="NAME", help="get settings for a specific game (repeatable); skips the library scan")
    p.add_argument("--resolution", type=_parse_resolution, metavar="WxH", help="override the detected resolution, e.g. 2560x1440")
    p.add_argument("--refresh", type=int, metavar="HZ", help="override the detected refresh rate")
    p.add_argument("--gpu", metavar="NAME", help="override the detected GPU, e.g. 'RTX 4070'")
    p.add_argument("--vram", type=float, metavar="GB", help="VRAM of the --gpu override")
    p.add_argument("--html", metavar="PATH", help="save a visual HTML report (and open it)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON instead of tables")
    p.add_argument("--details", action="store_true", help="show every setting for every game")
    p.add_argument("--list-games", action="store_true", help="list the games with built-in profiles")
    p.add_argument("--no-anim", action="store_true", help="skip the animations")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _hardware_panel(hw: Hardware) -> Panel:
    gpu = hw.gpu
    power, known = gpu_power(gpu)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    vram = f" · {gpu.vram_gb:g} GB" if gpu and gpu.vram_gb else ""
    grid.add_row("GPU", f"[bold]{gpu.name if gpu else 'Not detected'}[/]{vram}")
    bar_len = round(power / 5)
    grid.add_row("Power", f"[cyan]{'█' * bar_len}[/][dim]{'░' * (20 - bar_len)}[/] {power}/100" + ("" if known else " [dim](estimated)[/]"))
    grid.add_row("CPU", f"{hw.cpu_name} [dim]({hw.cpu_cores}C/{hw.cpu_threads}T)[/]")
    grid.add_row("RAM", f"{hw.ram_gb:g} GB")
    res = f"{hw.resolution[0]}x{hw.resolution[1]}" if hw.resolution else "Unknown (assuming 1920x1080)"
    grid.add_row("Display", f"{res} @ {hw.refresh_hz or '?'} Hz")
    return Panel(grid, title="[bold]Your rig[/]", border_style="cyan", expand=False)


def _summary_table(recs: list[Recommendation]) -> Table:
    t = Table(title="Recommended settings", title_style="bold", border_style="blue", header_style="bold cyan")
    t.add_column("Game", style="bold")
    t.add_column("Preset", justify="center")
    t.add_column("Upscaling")
    t.add_column("Est. FPS", justify="right")
    t.add_column("Target", justify="right", style="dim")
    t.add_column("Notes", style="dim")
    for r in recs:
        up = f"{r.upscaler} {r.upscale_mode}" if r.upscaler else "Native"
        fps_style = "green" if r.estimated_fps >= r.target_fps else "yellow" if r.estimated_fps >= r.target_fps * 0.75 else "red"
        notes = []
        if r.ray_tracing:
            notes.append("RT on")
        if r.frame_generation:
            notes.append("Frame gen")
        if r.bottleneck == "CPU":
            notes.append("CPU-bound")
        if not r.profile_known:
            notes.append("generic")
        t.add_row(r.game, Text(r.preset, style=PRESET_STYLE.get(r.preset, "")), up,
                  Text(f"~{r.estimated_fps}", style=fps_style), str(r.target_fps), ", ".join(notes))
    return t


def _detail_panel(r: Recommendation) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    for k, v in r.settings.items():
        grid.add_row(k, v)
    parts: list = [grid]
    if r.tips:
        tips = Text()
        for tip in r.tips:
            tips.append("  ➜ ", style="cyan")
            tips.append(tip + "\n")
        parts += [Text(""), tips]
    title = f"[bold]{r.game}[/]  [{PRESET_STYLE.get(r.preset, '')}]{r.preset}[/]  ~{r.estimated_fps} FPS"
    return Panel(Group(*parts), title=title, border_style="magenta", expand=False)


def _scan(console: Console, animate: bool, args) -> tuple[Hardware, list[InstalledGame]]:
    steps = ["Reading GPU and VRAM", "Checking CPU and memory", "Detecting display", "Searching Steam, Epic and GOG libraries"]
    hw: Hardware | None = None
    games: list[InstalledGame] = []
    with console.status("[cyan]Scanning your PC...", spinner="dots12") as status:
        for i, step in enumerate(steps):
            status.update(f"[cyan]{step}...")
            if i == 0:
                hw = detect()
            if i == 3 and not args.game:
                games = find_installed_games()
            if animate:
                time.sleep(0.35)
            console.print(f"  [green]✔[/] {step}")
    assert hw is not None
    return hw, games


def _utf8_output() -> None:
    """Windows uses a legacy code page when output is redirected; the banner needs UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.encoding and stream.encoding.lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    args = build_parser().parse_args(argv)
    console = Console(highlight=False)
    animate = not args.no_anim and not args.json and console.is_terminal
    profiles = load_profiles()

    if args.list_games:
        for p in sorted(profiles, key=lambda p: p.name.lower()):
            console.print(f"  • {p.name}  [dim]({p.style})[/]")
        return 0

    if not args.json:
        console.print(_gradient_banner())
        console.print()

    if args.json:
        hw, games = detect(), ([] if args.game else find_installed_games())
    else:
        hw, games = _scan(console, animate, args)

    if args.gpu:
        hw.gpus = [GPU(args.gpu, args.vram)]
    targets: list = args.game if args.game else games

    recs = [recommend(hw, match_profile(g, profiles), args.resolution, args.refresh) for g in targets]
    recs.sort(key=lambda r: r.game.lower())

    if args.json:
        print(json.dumps({"hardware": hw.to_dict(), "games": [r.to_dict() for r in recs]}, indent=2))
        return 0

    console.print()
    console.print(_hardware_panel(hw))
    if hw.gpu is None:
        console.print("[yellow]No GPU detected. Use --gpu \"RTX 3060\" --vram 12 to set it manually.[/]")

    if not recs:
        console.print("\n[yellow]No installed games found.[/] Try [bold]fps-tuner --game \"Cyberpunk 2077\"[/] "
                      "or [bold]--list-games[/] to see built-in profiles.")
        return 0

    console.print()
    console.print(_summary_table(recs))
    if args.details or args.game:
        for r in recs:
            console.print(_detail_panel(r))
    else:
        console.print("[dim]Tip: run with --details for every setting per game, or --html report.html for a visual report.[/]")

    if args.html:
        path = Path(args.html).resolve()
        path.write_text(report.render(hw, recs), encoding="utf-8")
        console.print(f"\n[green]✔[/] Report saved to [bold]{path}[/]")
        if animate:
            webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
