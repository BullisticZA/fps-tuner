import json

from fps_tuner import cli


def test_json_output_for_named_games(capsys):
    code = cli.main(["--json", "--gpu", "NVIDIA GeForce RTX 3070", "--vram", "8",
                     "--resolution", "2560x1440", "--refresh", "144", "-g", "Cyberpunk 2077", "-g", "Valorant"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hardware"]["gpu"] == "NVIDIA GeForce RTX 3070"
    games = {g["game"]: g for g in data["games"]}
    assert set(games) == {"Cyberpunk 2077", "Valorant"}
    assert games["Cyberpunk 2077"]["resolution"] == "2560x1440"


def test_html_report(tmp_path, capsys):
    out = tmp_path / "report.html"
    code = cli.main(["--no-anim", "--gpu", "AMD Radeon RX 6700 XT", "--vram", "12", "-g", "<b>Evil</b> Game",
                     "-g", "Red Dead Redemption 2", "--html", str(out)])
    assert code == 0
    html = out.read_text(encoding="utf-8")
    assert "Red Dead Redemption 2" in html and "AMD Radeon RX 6700 XT" in html
    assert "<b>Evil</b>" not in html and "&lt;b&gt;Evil&lt;/b&gt;" in html


def test_list_games(capsys):
    assert cli.main(["--list-games"]) == 0
    assert "Counter-Strike 2" in capsys.readouterr().out


def test_bad_resolution_is_rejected(capsys):
    try:
        cli.main(["--resolution", "huge", "-g", "x"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected argparse to exit")
