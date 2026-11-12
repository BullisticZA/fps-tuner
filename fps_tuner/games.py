"""Finds installed games (Steam, Epic and GOG) and matches them to known performance profiles."""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import vdf

NON_GAMES = re.compile(r"redistributable|proton|steam linux runtime|steamworks|\bsdk\b|dedicated server|soundtrack|benchmark", re.I)


@dataclass
class InstalledGame:
    name: str
    store: str
    path: str | None = None
    steam_appid: int | None = None
    size_gb: float | None = None


@dataclass
class GameProfile:
    name: str
    demand: int
    vram: int
    style: str = "cinematic"
    cpu_heavy: bool = False
    upscalers: list[str] = field(default_factory=list)
    rt_heavy: bool = False
    frame_gen: bool = False
    fps_cap: int | None = None
    tips: list[str] = field(default_factory=list)
    steam: int | None = None
    known: bool = True


def normalize(name: str) -> str:
    name = name.lower().replace("&", "and")
    name = re.sub(r"[™®©'’]", "", name)          # "Baldur's" == "Baldurs"
    name = re.sub(r"[:\-–_.,!]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def load_profiles() -> list[GameProfile]:
    raw = json.loads(resources.files("fps_tuner").joinpath("data/games.json").read_text(encoding="utf-8"))
    return [GameProfile(**g) for g in raw["games"]]


def default_profile(name: str) -> GameProfile:
    """Unknown games get a middle-of-the-road profile."""
    return GameProfile(name=name, demand=28, vram=6, upscalers=["FSR"], known=False,
                       tips=["Not in the profile database yet - settings are based on a typical modern game."])


def match_profile(game: InstalledGame | str, profiles: list[GameProfile]) -> GameProfile:
    name = game if isinstance(game, str) else game.name
    appid = None if isinstance(game, str) else game.steam_appid
    if appid:
        for p in profiles:
            if p.steam == appid:
                return p
    target = normalize(name)
    for p in profiles:
        if normalize(p.name) == target:
            return p
    # Partial match for names with editions, e.g. "Cyberpunk 2077 Ultimate Edition".
    candidates = [p for p in profiles if len(normalize(p.name)) >= 6 and target.startswith(normalize(p.name))]
    if candidates:
        return max(candidates, key=lambda p: len(p.name))
    return default_profile(name)


# ---------------------------------------------------------------- Steam

def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if platform.system() == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]) / "Steam")
    else:
        home = Path.home()
        roots += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
                  home / "Library" / "Application Support" / "Steam"]
    seen, unique = set(), []
    for r in roots:
        try:
            key = r.resolve()
        except OSError:
            continue
        if key not in seen and (r / "steamapps").is_dir():
            seen.add(key)
            unique.append(r)
    return unique


def steam_libraries(root: Path) -> list[Path]:
    libs = [root / "steamapps"]
    lf = root / "steamapps" / "libraryfolders.vdf"
    if lf.is_file():
        data = vdf.get_ci(vdf.loads(lf.read_text(encoding="utf-8", errors="ignore")), "libraryfolders", {})
        for entry in data.values():
            path = vdf.get_ci(entry, "path") if isinstance(entry, dict) else entry
            if path:
                libs.append(Path(path) / "steamapps")
    seen, unique = set(), []
    for lib in libs:
        k = str(lib).lower()
        if k not in seen and lib.is_dir():
            seen.add(k)
            unique.append(lib)
    return unique


def steam_games(roots: list[Path] | None = None) -> list[InstalledGame]:
    games = []
    for root in roots if roots is not None else steam_roots():
        for lib in steam_libraries(root):
            for acf in lib.glob("appmanifest_*.acf"):
                try:
                    state = vdf.get_ci(vdf.loads(acf.read_text(encoding="utf-8", errors="ignore")), "AppState", {})
                except ValueError:
                    continue
                name = vdf.get_ci(state, "name")
                if not name or NON_GAMES.search(name):
                    continue
                size = vdf.get_ci(state, "SizeOnDisk")
                installdir = vdf.get_ci(state, "installdir")
                games.append(InstalledGame(
                    name=name, store="Steam",
                    path=str(lib / "common" / installdir) if installdir else None,
                    steam_appid=int(vdf.get_ci(state, "appid", 0)) or None,
                    size_gb=round(int(size) / 1024**3, 1) if size and size.isdigit() else None,
                ))
    return games


# ---------------------------------------------------------------- Epic

def epic_games(manifest_dir: Path | None = None) -> list[InstalledGame]:
    if manifest_dir is None:
        program_data = os.environ.get("ProgramData")
        if not program_data:
            return []
        manifest_dir = Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    games = []
    for item in manifest_dir.glob("*.item") if manifest_dir.is_dir() else []:
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue
        name = data.get("DisplayName")
        if not name or NON_GAMES.search(name) or "UE_" in str(data.get("AppName", "")):
            continue
        size = data.get("InstallSize")
        games.append(InstalledGame(name=name, store="Epic", path=data.get("InstallLocation"),
                                   size_gb=round(int(size) / 1024**3, 1) if size else None))
    return games


# ---------------------------------------------------------------- GOG

def gog_games() -> list[InstalledGame]:
    if platform.system() != "Windows":
        return []
    games = []
    try:
        import winreg

        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games")
    except OSError:
        return []
    for i in range(512):
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        try:
            key = winreg.OpenKey(root, sub)
            name = winreg.QueryValueEx(key, "gameName")[0]
            path = winreg.QueryValueEx(key, "path")[0]
            if name and not NON_GAMES.search(name):
                games.append(InstalledGame(name=name, store="GOG", path=path))
        except OSError:
            continue
    return games


def find_installed_games() -> list[InstalledGame]:
    """All installed games across supported launchers, de-duplicated and sorted by name."""
    found: dict[str, InstalledGame] = {}
    for scan in (steam_games, epic_games, gog_games):
        try:
            for g in scan():
                found.setdefault(normalize(g.name), g)
        except OSError:
            continue
    return sorted(found.values(), key=lambda g: g.name.lower())
