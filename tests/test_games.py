import json

import pytest

from fps_tuner import vdf
from fps_tuner.games import InstalledGame, epic_games, load_profiles, match_profile, normalize, steam_games

PROFILES = load_profiles()


# ---------------------------------------------------------------- VDF parser

def test_vdf_nested_and_comments():
    text = '''
    // a comment
    "libraryfolders"
    {
        "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" "apps" { "730" "123" } }
        "1" { "path" "D:\\\\Games\\\\Steam" }
    }'''
    data = vdf.loads(text)
    assert data["libraryfolders"]["0"]["path"] == "C:\\Program Files (x86)\\Steam"
    assert data["libraryfolders"]["0"]["apps"]["730"] == "123"
    assert data["libraryfolders"]["1"]["path"] == "D:\\Games\\Steam"


def test_vdf_escaped_quotes_and_case_insensitive_get():
    data = vdf.loads('"AppState" { "name" "The \\"Best\\" Game" }')
    assert vdf.get_ci(data, "appstate")["name"] == 'The "Best" Game'


def test_vdf_rejects_unbalanced():
    with pytest.raises(ValueError):
        vdf.loads('"a" { "b" "c"')


# ---------------------------------------------------------------- Steam / Epic scanning

def _make_steam(tmp_path):
    root = tmp_path / "Steam"
    lib2 = tmp_path / "Games" / "SteamLibrary"
    (root / "steamapps").mkdir(parents=True)
    (lib2 / "steamapps").mkdir(parents=True)
    path_str = str(lib2).replace("\\", "\\\\")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{path_str}" }} }}', encoding="utf-8")

    def manifest(folder, appid, name, size="53687091200"):
        (folder / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            f'"AppState" {{ "appid" "{appid}" "name" "{name}" "installdir" "{name}" "SizeOnDisk" "{size}" }}',
            encoding="utf-8")

    manifest(root, 730, "Counter-Strike 2")
    manifest(root, 228980, "Steamworks Common Redistributables")
    manifest(lib2, 1091500, "Cyberpunk 2077")
    manifest(lib2, 1493710, "Proton Experimental")
    return root


def test_steam_scan_finds_games_across_libraries_and_skips_tools(tmp_path):
    games = steam_games([_make_steam(tmp_path)])
    names = sorted(g.name for g in games)
    assert names == ["Counter-Strike 2", "Cyberpunk 2077"]
    cp = next(g for g in games if g.steam_appid == 1091500)
    assert cp.size_gb == 50.0 and cp.store == "Steam"


def test_epic_scan(tmp_path):
    (tmp_path / "a.item").write_text(json.dumps({"DisplayName": "Alan Wake 2", "InstallLocation": "E:/AW2",
                                                 "AppName": "Dill", "InstallSize": 96636764160}), encoding="utf-8")
    (tmp_path / "b.item").write_text("not json", encoding="utf-8")
    games = epic_games(tmp_path)
    assert [g.name for g in games] == ["Alan Wake 2"] and games[0].size_gb == 90.0


# ---------------------------------------------------------------- Profile matching

def test_match_by_steam_appid_beats_name():
    g = InstalledGame(name="Some Renamed Title", store="Steam", steam_appid=1245620)
    assert match_profile(g, PROFILES).name == "ELDEN RING"


@pytest.mark.parametrize("name, expected", [
    ("ELDEN RING™", "ELDEN RING"),
    ("elden ring", "ELDEN RING"),
    ("Cyberpunk 2077: Ultimate Edition", "Cyberpunk 2077"),
    ("Baldurs Gate 3", "Baldur's Gate 3"),
])
def test_match_by_name_variants(name, expected):
    assert match_profile(name, PROFILES).name == expected


def test_prefix_match_prefers_longest_profile():
    assert match_profile("Grand Theft Auto V Enhanced Edition", PROFILES).name == "Grand Theft Auto V Enhanced"


def test_normalize():
    assert normalize("Tom Clancy's® Rainbow Six®  Siege") == "tom clancys rainbow six siege"


def test_profile_database_is_valid():
    names = [p.name for p in PROFILES]
    assert len(names) == len(set(names)) and len(names) >= 40
    for p in PROFILES:
        assert 1 <= p.demand <= 100 and 1 <= p.vram <= 24
        assert p.style in ("competitive", "cinematic")
        assert set(p.upscalers) <= {"DLSS", "FSR", "XeSS"}
